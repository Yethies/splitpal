import os
import requests
from dotenv import load_dotenv

load_dotenv()
r = requests.post(
    "https://api-m.sandbox.paypal.com/v1/oauth2/token",
    auth=(os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET")),
    data={"grant_type": "client_credentials"},
)
print(r.status_code)
print(r.json().get("access_token", r.text)[:40])
