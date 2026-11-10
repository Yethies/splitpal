import base64
import os
import threading
import uuid
from decimal import Decimal

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

from ai_split import ask_gemini, validate_split, SplitError

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)

# PayPal credentials from environment
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_API_BASE = "https://api-m.sandbox.paypal.com"

# Allowed payment range (USD) - protects against typos or tampered amounts
MIN_AMOUNT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000.00")

# --- In-memory storage (cleared when the server restarts) ---
SPLITS = {}
ORDERS = {}
LOCK = threading.Lock()


def get_access_token():
    """Request an OAuth 2.0 access token from PayPal sandbox."""
    url = f"{PAYPAL_API_BASE}/v1/oauth2/token"
    response = requests.post(
        url,
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    return response.json().get("access_token")


def paypal_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_access_token()}",
    }


def serialize_person(p):
    """Consistent public dict for each person including itemized breakdown and PayPal receipt ref."""
    return {
        "name": p["name"],
        "amount": p["amount"],
        "status": p["status"],
        "subtotal": p.get("subtotal", p["amount"]),
        "tax_amount": p.get("tax_amount", "0.00"),
        "items": p.get("items", []),
        "excluded": p.get("excluded", []),
        "capture_id": p.get("capture_id"),
    }


@app.route("/")
def index():
    """Serve the host/organizer dashboard."""
    return render_template("index.html", client_id=PAYPAL_CLIENT_ID)


@app.route("/split", methods=["POST"])
def split_bill():
    """Send bill text (and optional receipt photo) to AI, verify with our math, and save shares."""
    data = request.get_json(silent=True) or {}
    bill_text = str(data.get("bill", "")).strip()
    image_b64 = data.get("image_base64")
    image_mime = str(data.get("image_mime", "image/jpeg")).strip()

    if image_b64 and "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    if not bill_text and not image_b64:
        return jsonify({"error": "Please paste a bill, upload a receipt photo, or speak into the mic"}), 400
    if len(bill_text) > 2000:
        return jsonify({"error": "Bill text is too long (max 2000 characters)"}), 400

    try:
        ai_answer = ask_gemini(bill_text=bill_text, image_base64=image_b64, image_mime=image_mime)
        result = validate_split(ai_answer)
    except SplitError as e:
        return jsonify({"error": str(e)}), 422

    # Save verified shares on the server
    split_id = uuid.uuid4().hex
    people = []
    # Default: first person is the one who paid the restaurant bill
    for i, s in enumerate(result["shares"]):
        is_payer = (i == 0)
        status = "Covered Bill" if is_payer else "Pending"
        people.append({
            "name": s["name"],
            "amount": s["amount"],
            "subtotal": s.get("subtotal", s["amount"]),
            "tax_amount": s.get("tax_amount", "0.00"),
            "items": s.get("items", []),
            "excluded": s.get("excluded", []),
            "status": status,
            "order_id": None,
            "capture_id": None,
        })

    with LOCK:
        SPLITS[split_id] = {
            "total": result["total"],
            "tax_percent": result.get("tax_percent", "0"),
            "payer_index": 0,
            "people": people,
        }

    result["split_id"] = split_id
    result["payer_index"] = 0
    result["people"] = [serialize_person(p) for p in people]
    return jsonify(result)


@app.route("/split-voice", methods=["POST"])
def split_voice():
    """Accept raw audio recording, send to Gemini multimodal API, and split bill."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file received"}), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    print(f"[SplitPal] Received audio file: {len(audio_bytes)} bytes")
    if len(audio_bytes) < 800:
        return jsonify({"error": "Audio recording was too short or empty. Please speak for at least 3-4 seconds."}), 400

    raw_mime = audio_file.content_type or "audio/webm"
    # Clean MIME type without parameters (e.g. 'audio/webm;codecs=opus' -> 'audio/webm')
    mime_type = raw_mime.split(";")[0].strip() or "audio/webm"
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    try:
        ai_answer = ask_gemini(audio_base64=audio_b64, mime_type=mime_type)
        result = validate_split(ai_answer)
    except SplitError as e:
        print(f"[SplitPal] Voice processing error: {e}")
        return jsonify({"error": str(e)}), 422

    split_id = uuid.uuid4().hex
    people = []
    for i, s in enumerate(result["shares"]):
        is_payer = (i == 0)
        people.append({
            "name": s["name"],
            "amount": s["amount"],
            "subtotal": s.get("subtotal", s["amount"]),
            "tax_amount": s.get("tax_amount", "0.00"),
            "items": s.get("items", []),
            "excluded": s.get("excluded", []),
            "status": "Covered Bill" if is_payer else "Pending",
            "order_id": None,
            "capture_id": None,
        })

    with LOCK:
        SPLITS[split_id] = {
            "total": result["total"],
            "tax_percent": result.get("tax_percent", "0"),
            "payer_index": 0,
            "people": people,
        }

    result["split_id"] = split_id
    result["payer_index"] = 0
    result["people"] = [serialize_person(p) for p in people]
    return jsonify(result)


@app.route("/split/<split_id>/set-payer", methods=["POST"])
def set_payer(split_id):
    """Change who covered the bill at the restaurant."""
    data = request.get_json(silent=True) or {}
    try:
        payer_idx = int(data.get("payer_index", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid payer index"}), 400

    with LOCK:
        split = SPLITS.get(split_id)
        if not split or not (0 <= payer_idx < len(split["people"])):
            return jsonify({"error": "Split or person not found"}), 404

        split["payer_index"] = payer_idx
        for i, p in enumerate(split["people"]):
            if i == payer_idx:
                p["status"] = "Covered Bill"
            elif p["status"] == "Covered Bill":
                p["status"] = "Pending"

        return jsonify({
            "people": [serialize_person(p) for p in split["people"]],
            "payer_index": payer_idx,
        })


@app.route("/split/<split_id>/status")
def split_status(split_id):
    """Return live status of the split for real-time polling on host page."""
    with LOCK:
        split = SPLITS.get(split_id)
        if not split:
            return jsonify({"error": "Split not found"}), 404

        return jsonify({
            "total": split["total"],
            "payer_index": split.get("payer_index", 0),
            "people": [serialize_person(p) for p in split["people"]],
        })


@app.route("/pay/<split_id>/<int:person_index>")
def pay_page(split_id, person_index):
    """Personal pay page for a specific friend (e.g. sent via WhatsApp/link)."""
    with LOCK:
        split = SPLITS.get(split_id)
        if not split or not (0 <= person_index < len(split["people"])):
            return "<h3>Payment link not found or expired.</h3>", 404

        person = split["people"][person_index]
        payer_idx = split.get("payer_index", 0)
        payer_name = (
            split["people"][payer_idx]["name"]
            if 0 <= payer_idx < len(split["people"])
            else "Organizer"
        )

    return render_template(
        "pay.html",
        client_id=PAYPAL_CLIENT_ID,
        split_id=split_id,
        person_index=person_index,
        person_name=person["name"],
        amount=person["amount"],
        status=person["status"],
        payer_name=payer_name,
        total=split["total"],
        tax_percent=split.get("tax_percent", "0"),
        person=serialize_person(person),
    )


@app.route("/create-order", methods=["POST"])
def create_order():
    """Create a PayPal order for ONE person's share."""
    data = request.get_json(silent=True) or {}
    split_id = data.get("split_id")
    try:
        index = int(data.get("person"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid person"}), 400

    with LOCK:
        split = SPLITS.get(split_id)
        if not split or not (0 <= index < len(split["people"])):
            return jsonify({"error": "This split was not found. Please split again."}), 404
        person = split["people"][index]
        if person["status"] == "Covered Bill":
            return jsonify({"error": f"{person['name']} covered the bill at the restaurant!"}), 409
        if person["status"] == "Paid":
            return jsonify({"error": f"{person['name']} has already paid"}), 409
        amount = Decimal(person["amount"])

    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        return jsonify({"error": f"Amount must be between {MIN_AMOUNT} and {MAX_AMOUNT} USD"}), 400

    item_names = ", ".join(i["name"] for i in person.get("items", []))
    desc = f"SplitPal ({person['name']}): {item_names}" if item_names else f"SplitPal share for {person['name']}"

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "description": desc[:127],
                "amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
            }
        ],
    }
    response = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders", headers=paypal_headers(), json=payload, timeout=30
    )
    order = response.json()
    if response.status_code not in (200, 201) or "id" not in order:
        return jsonify({"error": "PayPal could not create the order"}), 502

    with LOCK:
        ORDERS[order["id"]] = (split_id, index)
        person["order_id"] = order["id"]
    return jsonify({"id": order["id"]})


@app.route("/capture-order/<order_id>", methods=["POST"])
def capture_order(order_id):
    """Capture an approved order, then mark that person as Paid."""
    with LOCK:
        if order_id not in ORDERS:
            return jsonify({"error": "Unknown order"}), 404
        split_id, index = ORDERS[order_id]
        person = SPLITS[split_id]["people"][index]
        if person["status"] == "Paid":
            return jsonify({"error": f"{person['name']} has already paid"}), 409

    response = requests.post(
        f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture", headers=paypal_headers(), timeout=30
    )
    result = response.json()

    try:
        capture = result["purchase_units"][0]["payments"]["captures"][0]
        paid_ok = (
            result.get("status") == "COMPLETED"
            and capture["status"] == "COMPLETED"
            and capture["amount"]["currency_code"] == "USD"
            and Decimal(capture["amount"]["value"]) == Decimal(person["amount"])
        )
        capture_id = capture.get("id")
    except (KeyError, IndexError, TypeError):
        paid_ok = False
        capture_id = None

    if not paid_ok:
        return jsonify({"error": "Payment was not completed", "status": result.get("status")}), 400

    with LOCK:
        person["status"] = "Paid"
        person["capture_id"] = capture_id
    return jsonify({
        "status": "COMPLETED",
        "name": person["name"],
        "amount": person["amount"],
        "capture_id": capture_id,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
