# 💸 SplitPal — AI Bill Splitter & Instant PayPal Settlement

**SplitPal** turns messy group restaurant bills—typed notes, real-time voice recordings, or uploaded receipt photos with exceptions like *"Ravi didn't have starters"*—into **cent-verified individual PayPal checkouts**, shareable friend payment links, QR codes, and WhatsApp payment requests.

---

## ✨ Key Features

1. **Multimodal Input (Text + Receipt Photo Vision + Real-Time Voice)**
   - **Paste Text**: Describe who attended, items, prices, tax, and who skipped what in plain English.
   - **📷 Receipt Photo Upload + 🧾 1-Click Sample Receipt**: Upload a photo of a physical receipt (or click **Sample Receipt Photo** to generate one on the fly) alongside natural-language splitting rules.
   - **🎙️ Real-Time Voice Transcription**: Speak your bill and watch words transcribe live into the editor before splitting.

2. **AI Reads, Deterministic Python Does the Money Math (`ai_split.py`)**
   - Gemini extracts structured JSON (`people`, `items`, `shared_by`, `tax_percent`).
   - Our own Python engine (`validate_split` & `calculate_shares`) recomputes every item share using `Decimal` arithmetic and the **largest-remainder method**, guaranteeing that individual shares **always sum to the exact cent of the total bill** before any payment is created.

3. **Transparent Itemized Breakdown ("Why Am I Paying This?")**
   - Every person's card (and their personal friend pay page) shows the exact items they shared, the split fraction (`1/3`, `1/2`), their tax share, and any **excluded items** (`🚫 Excluded: 2 paneer starters`).

4. **End-to-End PayPal Orders v2 Settlement & Receipt Proof (`app.py`)**
   - Select who covered the restaurant bill (`Covered Bill` badge).
   - Friends can pay inline on the host dashboard, scan their personal **Show/Hide QR Code**, open their dedicated **Friend View** (`/pay/<split_id>/<person_index>`), or receive a pre-filled **💬 WhatsApp** payment link.
   - Server-side `/create-order` and `/capture-order/<order_id>` verify the captured currency (`USD`) and exact cent amount, store the official **PayPal Capture Reference ID (`PayPal Ref #...`)**, update live via 3-second polling, and trigger a **confetti celebration** when the bill is 100% settled.

---

## 🚀 Quick Start (Local Setup)

### 1. Clone & Create Virtual Environment
```bash
git clone <your-repo-url>
cd splitpal
python -m venv venv
# Windows PowerShell:
.\venv\Scripts\Activate.ps1
# macOS / Linux:
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure `.env`
Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```
- **`PAYPAL_CLIENT_ID` & `PAYPAL_CLIENT_SECRET`**: From your [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/applications/sandbox) (use a **US** Sandbox Business account & US Personal buyer account so repeated USD sandbox payments succeed without regional currency blocks).
- **`GEMINI_API_KEY`**: From [Google AI Studio](https://aistudio.google.com/apikey).

### 4. Run the App
```bash
python app.py
```
Open **http://localhost:5000** in Chrome or Edge.

### 5. Run Automated Verification Tests
```bash
python test_features.py
```

---

## 🛠️ Architecture & Files

- [`app.py`](app.py) — Flask server, PayPal OAuth2 & Orders v2 (`/create-order`, `/capture-order/<order_id>`), live status polling (`/split/<split_id>/status`), and friend payment route (`/pay/<split_id>/<person_index>`).
- [`ai_split.py`](ai_split.py) — Gemini API multimodal client (`ask_gemini`) + deterministic `Decimal` validation and largest-remainder cent distribution (`validate_split`, `calculate_shares`).
- [`templates/index.html`](templates/index.html) — Liquid-bubble glassmorphic organizer dashboard with Dark/Light mode, real-time voice transcription, receipt photo uploader + sample receipt generator, QR code toggle, WhatsApp share, and PayPal buttons.
- [`templates/pay.html`](templates/pay.html) — Mobile-ready friend checkout page showing their itemized receipt breakdown, excluded items, PayPal checkout button, and transaction receipt reference.
- [`render.yaml`](render.yaml) — 1-click cloud deployment blueprint for Render.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
