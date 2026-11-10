import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(f"API key loaded: {'Yes' if api_key else 'No'}")

url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
body = {
    "contents": [{"parts": [{"text": "Say hello in 3 words"}]}]
}

try:
    r = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=15
    )
    print("Status code:", r.status_code)
    print("Response:", r.text[:200])
except Exception as e:
    print("Error:", e)
