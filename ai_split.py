"""
AI step for SplitPal.

1. ask_gemini(): sends the messy bill text to Gemini and gets back structured JSON
   (people, items, who shared what, tax).
2. validate_split(): checks the AI's answer, then OUR OWN code calculates every share
   exactly. The AI reads; the code does the money math.
"""
import json
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

import requests

CENT = Decimal("0.01")
RETRIES_PER_MODEL = 2  # retry once if busy
MAX_SHARE = Decimal("1000.00")  # same limit as the payment route


class SplitError(Exception):
    """Raised when the AI output is missing, broken, or the math doesn't add up."""


# Instructions for the AI
PROMPT = """You split restaurant bills between friends.
Analyze the bill provided (which may be text, a receipt image, or a spoken audio recording).

Rules:
- If audio is provided, also set "transcription" to the exact transcript of what was spoken.
- If a receipt image is provided, extract every food/drink line item, its line total price, and any tax/service percentage from the image, and combine it with the text notes (which tell you the friends' names and any exceptions like who skipped what).
- "people": every person's name mentioned. If no names are given anywhere, set "error" to "Please mention the names of the friends splitting the bill (e.g. People: Yethies, Ravi, Anu)".
- "items": each line item with its TOTAL line price (e.g. "2 starters 4.00" -> price 4.00)
  and "shared_by": the names of the people who shared it. If not said otherwise,
  an item is shared by everyone.
- "tax_percent": the tax or service charge percentage (0 if none). If the receipt shows a tax dollar amount instead of a percentage, calculate tax_percent = (tax_amount / subtotal) * 100 rounded to 2 decimal places.
- "shares": each person's final amount = their part of each item (split equally among
  "shared_by") plus the same tax percent. Round to 2 decimals. The shares MUST add up
  exactly to the bill total; put any leftover cent on the first person.
- If the bill is unclear or impossible to split, fill "error" with a short reason.
"""

# Exact JSON shape we want back from Gemini
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transcription": {"type": "STRING"},
        "people": {"type": "ARRAY", "items": {"type": "STRING"}},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "price": {"type": "NUMBER"},
                    "shared_by": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["name", "price", "shared_by"],
            },
        },
        "tax_percent": {"type": "NUMBER"},
        "shares": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "amount": {"type": "NUMBER"},
                },
                "required": ["name", "amount"],
            },
        },
        "error": {"type": "STRING"},
    },
    "required": ["people", "items", "tax_percent", "shares"],
}


def ask_gemini(bill_text=None, audio_base64=None, mime_type="audio/webm", image_base64=None, image_mime="image/jpeg"):
    """Send the bill text, receipt image, or audio to Gemini and return its answer as a Python dict."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SplitError("GEMINI_API_KEY is missing from .env")

    # Multimodal audio requires gemini-3.8-flash
    if audio_base64:
        models = ["gemini-3.8-flash"]
    else:
        models = [
            m.strip()
            for m in os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-3.8-flash").split(",")
            if m.strip()
        ]

    clean_mime = (mime_type or "audio/webm").split(";")[0].strip()
    clean_img_mime = (image_mime or "image/jpeg").split(";")[0].strip()

    parts = []
    if audio_base64:
        parts.append({
            "inlineData": {
                "mimeType": clean_mime,
                "data": audio_base64
            }
        })
        parts.append({
            "text": PROMPT + "\nListen carefully to the attached voice recording of friends splitting a restaurant bill. Transcribe what was spoken and calculate the split."
        })
    elif image_base64:
        parts.append({
            "inlineData": {
                "mimeType": clean_img_mime,
                "data": image_base64
            }
        })
        notes = f"\nFriends & splitting rules:\n{bill_text}" if bill_text else "\nSplit all receipt items equally among the people listed."
        parts.append({
            "text": PROMPT + "\nRead all items, prices, and tax from the attached receipt image." + notes
        })
    else:
        parts.append({"text": PROMPT + f"\nBill:\n{bill_text}"})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
        },
    }

    last_error = "unknown error"
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(RETRIES_PER_MODEL):
            print(f"[SplitPal] Sending request to {model} (attempt {attempt + 1})...")
            try:
                # Key goes in a header (not the URL) so it never shows up in logs
                r = requests.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=30,
                )
            except requests.RequestException as err:
                last_error = f"Network timeout reaching AI service: {err}"
                print(f"[SplitPal] {last_error}")
                break  # try next model

            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    return json.loads(text)
                except (KeyError, IndexError, ValueError) as parse_err:
                    print(f"[SplitPal] JSON parse error: {parse_err}")
                    raise SplitError("The AI returned an answer we could not parse. Please try again.")

            # Show Gemini's error message (never contains the key)
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = ""
            last_error = f"AI error ({r.status_code}) on {model}: {msg}"
            print(f"[SplitPal] {last_error} (attempt {attempt + 1})")

            if r.status_code in (429, 500, 503):
                if attempt < RETRIES_PER_MODEL - 1:
                    time.sleep(2)
                continue
            break  # move to next model if available

    raise SplitError(last_error)


def to_money(value, what):
    """Turn a number from the AI into an exact Decimal, or fail clearly."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise SplitError(f"{what} is not a valid number")
    if not d.is_finite() or d < 0:
        raise SplitError(f"{what} must be a positive number")
    return d


def validate_split(data):
    """
    Check the AI's answer with our own math. Returns a clean result, or raises SplitError.
    Nobody should ever be charged an amount that fails these checks.
    """
    if not isinstance(data, dict):
        raise SplitError("The AI returned an unexpected format")
    if data.get("error"):
        raise SplitError(f"AI could not split this bill: {data['error']}")

    # --- People ---
    people = [str(p).strip() for p in data.get("people", []) if str(p).strip()]
    if not people:
        raise SplitError("No people found. Mention everyone's name in the bill.")
    if len(set(people)) != len(people):
        raise SplitError("The same name appears twice. Use unique names.")

    # --- Items: recompute the bill ourselves ---
    items = data.get("items", [])
    if not items:
        raise SplitError("No items found in the bill")

    subtotal = Decimal("0")
    expected = {p: Decimal("0") for p in people}  # what WE think each person owes before tax
    person_items = {p: [] for p in people}
    person_excluded = {p: [] for p in people}

    for item in items:
        name = str(item.get("name", "item")).strip()
        price = to_money(item.get("price"), f"Price of '{name}'")
        sharers = list(dict.fromkeys(str(s).strip() for s in item.get("shared_by", [])))  # no duplicates
        if not sharers:
            raise SplitError(f"Nobody is assigned to '{name}'")
        for s in sharers:
            if s not in expected:
                raise SplitError(f"'{s}' shares '{name}' but is not in the people list")
        subtotal += price
        item_share = price / len(sharers)
        for p in people:
            if p in sharers:
                expected[p] += item_share
                person_items[p].append({
                    "name": name,
                    "share": f"{item_share.quantize(CENT, rounding=ROUND_HALF_UP):.2f}",
                    "split_way": len(sharers),
                })
            else:
                person_excluded[p].append(name)

    tax_percent = to_money(data.get("tax_percent", 0), "Tax percent")
    if tax_percent > 100:
        raise SplitError("Tax percent looks wrong (over 100%)")
    multiplier = 1 + tax_percent / 100
    bill_total = (subtotal * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)
    if bill_total <= 0:
        raise SplitError("The bill total is zero")

    # --- OUR CODE calculates every share exactly ---
    shares = calculate_shares(people, expected, multiplier, bill_total)

    # SAFETY CHECK: shares must add up EXACTLY to the bill total
    if sum(shares.values()) != bill_total:
        raise SplitError("Internal check failed: shares don't add up. Not charging anyone.")
    for p, amount in shares.items():
        if amount > MAX_SHARE:
            raise SplitError(f"Share of '{p}' is above the {MAX_SHARE} limit")

    return {
        "transcription": data.get("transcription", ""),
        "items": [
            {"name": i.get("name"), "price": f"{Decimal(str(i['price'])):.2f}", "shared_by": i["shared_by"]}
            for i in items
        ],
        "tax_percent": f"{tax_percent.normalize():f}",
        "total": f"{bill_total:.2f}",
        "shares": [
            {
                "name": p,
                "amount": f"{shares[p]:.2f}",
                "subtotal": f"{expected[p].quantize(CENT, rounding=ROUND_HALF_UP):.2f}",
                "tax_amount": f"{(shares[p] - expected[p].quantize(CENT, rounding=ROUND_HALF_UP)):.2f}",
                "items": person_items[p],
                "excluded": person_excluded[p],
            }
            for p in people
        ],
        "ai_check": compare_ai_shares(data.get("shares"), shares),
    }


def calculate_shares(people, expected, multiplier, bill_total):
    """
    Turn exact (unrounded) amounts into cents that add up EXACTLY to bill_total.
    Method: round everyone down to the cent, then hand out the leftover cents
    one by one to the people who lost the most in rounding ("largest remainder").
    """
    exact = {p: expected[p] * multiplier for p in people}
    shares = {p: exact[p].quantize(CENT, rounding=ROUND_DOWN) for p in people}

    leftover_cents = int((bill_total - sum(shares.values())) / CENT)
    by_remainder = sorted(people, key=lambda p: exact[p] - shares[p], reverse=True)
    for i in range(leftover_cents):
        shares[by_remainder[i % len(people)]] += CENT
    return shares


def compare_ai_shares(ai_shares, our_shares):
    """Cross-check: did the AI's own math agree with ours? (Only informational.)"""
    try:
        ai = {str(s["name"]).strip(): Decimal(str(s["amount"])) for s in ai_shares}
        if set(ai) == set(our_shares) and all(
            abs(ai[p] - our_shares[p]) <= CENT for p in our_shares
        ):
            return "AI math matched our exact calculation"
    except (TypeError, KeyError, InvalidOperation):
        pass
    return "AI math was off, so SplitPal used its own exact calculation"

