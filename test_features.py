"""Automated verification of all SplitPal features."""
from decimal import Decimal
from ai_split import validate_split
from app import app, SPLITS, ORDERS

# 1. Verify validate_split itemized breakdown & excluded items
sample_ai_output = {
    "people": ["Yethies", "Ravi", "Anu"],
    "items": [
        {"name": "Biryani", "price": 3.50, "shared_by": ["Yethies", "Ravi", "Anu"]},
        {"name": "2 paneer starters", "price": 4.00, "shared_by": ["Yethies", "Anu"]},
        {"name": "3 juices", "price": 2.70, "shared_by": ["Yethies", "Ravi", "Anu"]},
    ],
    "tax_percent": 5,
    "shares": [
        {"name": "Yethies", "amount": 4.27},
        {"name": "Ravi", "amount": 2.17},
        {"name": "Anu", "amount": 4.27},
    ],
}

res = validate_split(sample_ai_output)
assert res["total"] == "10.71", f"Expected 10.71, got {res['total']}"
assert sum(Decimal(s["amount"]) for s in res["shares"]) == Decimal("10.71")

ravi = next(s for s in res["shares"] if s["name"] == "Ravi")
assert ravi["amount"] == "2.17"
assert "2 paneer starters" in ravi["excluded"], "Ravi should have 2 paneer starters in excluded list"
assert len(ravi["items"]) == 2, "Ravi should have 2 shared items"

# 2. Verify Flask routes and templates with test_client
client = app.test_client()

home = client.get("/")
assert home.status_code == 200
html = home.get_data(as_text=True)
assert "loadSampleReceipt()" in html
assert "📷 Receipt Photo" in html
assert "💬 WhatsApp" in html
assert "🙈 Hide QR" in html

# Logo + favicons are linked and served
assert "/static/img/icon-192.png" in html
assert 'property="og:image"' in html
for f in ["icon-32.png", "icon-180.png", "icon-192.png", "logo-wordmark.jpg", "logo-icon.jpg"]:
    r = client.get(f"/static/img/{f}")
    assert r.status_code == 200, f"Logo file missing: {f}"
    r.close()

# Seed a split in SPLITS and test /status, /set-payer, and /pay/<id>/<idx>
split_id = "testsplit123"
SPLITS[split_id] = {
    "total": res["total"],
    "tax_percent": res["tax_percent"],
    "payer_index": 0,
    "people": [
        {
            **s,
            "status": "Covered Bill" if i == 0 else "Pending",
            "order_id": None,
            "capture_id": "9XK123456789" if i == 2 else None,
        }
        for i, s in enumerate(res["shares"])
    ],
}
SPLITS[split_id]["people"][2]["status"] = "Paid"

st = client.get(f"/split/{split_id}/status").get_json()
assert st["people"][1]["excluded"] == ["2 paneer starters"]
assert st["people"][2]["capture_id"] == "9XK123456789"

pay_ravi = client.get(f"/pay/{split_id}/1")
assert pay_ravi.status_code == 200
pay_html = pay_ravi.get_data(as_text=True)
assert "Excluded from your bill: 2 paneer starters" in pay_html
assert "Biryani" in pay_html

pay_anu = client.get(f"/pay/{split_id}/2")
assert pay_anu.status_code == 200
assert "PayPal Ref #9XK123456789" in pay_anu.get_data(as_text=True)

# 3. Verify live /split endpoint with Gemini
live_resp = client.post(
    "/split",
    json={"bill": "People: Yethies, Ravi, Anu. Biryani 3.50, 2 paneer starters 4.00, 3 juices 2.70, tax 5%, Ravi didn't have starters"},
)
assert live_resp.status_code == 200, f"Live /split failed: {live_resp.get_data(as_text=True)}"
live_data = live_resp.get_json()
assert live_data["total"] == "10.71"
ravi_live = next(p for p in live_data["people"] if p["name"] == "Ravi")
assert ravi_live["amount"] == "2.17"
assert len(ravi_live["excluded"]) == 1

print("ALL SPLITPAL FEATURE TESTS PASSED SUCCESSFULLY!")
