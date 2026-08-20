# Job Outreach Automation & CRM Dashboard

An end-to-end system for automating personalized job outreach on WhatsApp (with PDF resume attachments) and tracking candidate responses in a local CRM dashboard. Managed with **`uv`** and **`pyproject.toml`**.

---

## 📁 Project Structure

```text
D:\Reachout\
├── pyproject.toml             # uv / PEP 621 project configuration & dependencies
├── README.md                  # Documentation and quickstart guide
├── send_mnc_whatsapp.py       # WhatsApp automation engine (Text + PDF resume sender)
├── crm_server.py              # FastAPI CRM backend server & REST API
├── clean_mnc.py               # Excel cleaning script (transforms raw sheets into MNC_Final.xlsx)
├── send_people_email.py       # Automated email outreach utility
├── data/
│   ├── MNC_Final.xlsx         # Primary cleaned contact database (175+ contacts across 164 companies)
│   ├── Reachout.xlsx          # Original raw multi-sheet workbook (backup)
│   ├── mnc_cleaned_contacts.csv # Cleaned contact export
│   └── crm_data.json          # Persistent CRM notes, custom statuses, and follow-ups
├── logs/
│   └── mnc_whatsapp_send_log.csv # Live delivery log tracking all sent WhatsApp messages
├── ui/
│   └── crm_dashboard.html     # Interactive CRM Web Dashboard (Single-Page App)
└── .whatsapp_session/         # Persistent WhatsApp Web browser login profile
```

---

## 🚀 Quick Start with `uv`

### 1. Launch the CRM Dashboard
```powershell
uv run python crm_server.py
```
*(or simply `python crm_server.py`)*

👉 Open your browser at: **[http://localhost:8000](http://localhost:8000)**

---

### 2. WhatsApp Outreach Automation

#### A. Safe Dry-Run (Preview only, sends nothing)
```powershell
uv run python send_mnc_whatsapp.py
```

#### B. Send in Small Batches (Recommended for testing)
```powershell
uv run python send_mnc_whatsapp.py --send --limit 5
```

#### C. Send to Everyone (Full Run)
```powershell
uv run python send_mnc_whatsapp.py --send
```

#### D. Single Number Test
```powershell
uv run python send_mnc_whatsapp.py --phone 917499718082 --send
```

---

## ⚡ Key Highlights

* **Automatic Multi-Number Parsing:** Handles comma-separated numbers (e.g. `+919980656407, +916364866859`), automatically creating distinct outreach records for each number.
* **Verified PDF Delivery:** Intercepts browser file chooser and verifies document delivery inside the chat.
* **Integrated CRM Tracking:** Update outcomes (`Replied - Interested`, `No Openings`, `Referral Given`, `Follow-up Needed`) with 1 click directly from the web dashboard.
