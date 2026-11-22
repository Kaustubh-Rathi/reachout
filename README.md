# Reachout — Multi-Channel Enterprise Recruitment & Outreach Platform

Reachout is a self-hosted recruiting CRM that sends templated **WhatsApp** and **email** outreach to HR contacts and tracks every reply, send, failure, and follow-up.

It runs as a single local FastAPI process: the same server serves the JSON API and the web dashboard, and all state lives in SQLite. There is no cloud dependency, no build step, and no Node runtime needed to use it.

- **Company-first round-robin dispatch** — contacts are interleaved across companies so no employer receives simultaneous outreach, with quota-aware campaign pacing.
- **Multi-endpoint HR coverage** — every phone number and email address on a contact is tracked and dispatched independently, with per-endpoint state, retry, and exclusion rules.
- **Two real channels** — WhatsApp Web through Camoufox (Firefox) and SMTP email, sharing one template and scheduling model.
- **Provider-compliant pacing** — per-sender inter-message spacing with human jitter, hourly/daily caps, and exponential backoff, configurable per channel.
- **Encrypted credential vault** — SMTP secrets are stored with HMAC-SHA256 authenticated encryption over PBKDF2-derived keys, never in plaintext database rows.
- **Transactional crash recovery** — every dispatch moves through `PREPARED → SENDING → SENT / FAILED`; orphaned in-flight attempts surface in the Recovery Queue instead of being silently retried.
- **Lossless source sync** — Excel/CSV imports are fingerprinted and never mutate existing records or historical audit logs.
- **Live operations dashboard** — a dependency-free SPA with KPI overview, contact hierarchy views, sender health, templates, campaign controls, and a WebSocket event stream.

---

## 🚀 Quick Start

### 1. Prerequisites

- Python **3.10+**
- [`uv`](https://github.com/astral-sh/uv) (recommended), or a plain virtual environment
- Node.js **18+** — only for the UI unit tests (`npm test`), not for running the app

```powershell
uv sync
```

### 2. Start the application

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open **[http://localhost:8000](http://localhost:8000)**.

> If `uv` is not available, use the project virtual environment directly:
>
> ```powershell
> .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
> ```

The first start creates `data/reachout.db` automatically. That is the **real** database the app uses unless you point `DATABASE_URL` somewhere else.

### 3. First-run checklist

Work through these in order — the dashboard is designed around this flow.

1. **Add a sender** → **Senders**
   - WhatsApp: click **Re-authenticate** on a sender row and scan the QR code.
   - Email: click **Reconfigure**, enter SMTP host/port/username/app password, then **Test and verify**.
2. **Create a template** → **Templates** → **+ Add Template**. Templates hold the message body, optional email subject, and an optional attachment path.
3. **Bring in contacts** → **Sync** in the header. Point it at an Excel/CSV source. Re-syncing is safe: existing records and history are never overwritten.
4. **Send a message** → **Contacts** → open a company → **Send**. Pick the endpoint, sender, and template, then review the prefilled message.
5. **Watch progress** → **Overview** shows readiness, campaign state, delivery KPIs, failed attempts, and follow-ups due.

---

## 🎬 Demo Video

A 2 min 35 s narrated walkthrough: the problem, setup, the live dashboard, and the crash-recovery code path.

| | |
| :--- | :--- |
| Runtime | 2:35 (1920x1080, 30 fps, burned-in captions) |
| Narration | Windows built-in speech (SAPI) — no cloud TTS, no API key |
| Footage | the real app on `127.0.0.1:8000`, reading `data/demo.db` |
| Files | `out/reachout-voiced.mp4`, `out/reachout-captions-only.mp4`, `out/reachout.srt` |

`out/` is ignored by Git, so these files exist only on the machine that recorded them. They are build artifacts, not part of the checkout, and the scene-recording scripts are not part of this repository either.

| Start | Section |
| :--- | :--- |
| 0:00 | The problem — duplicate outreach, dead endpoints, missing records |
| 0:16 | Setup — `uv sync`, then one uvicorn command on port 8000 |
| 0:43 | The live dashboard — KPIs, company-first pacing, per-endpoint state, the send dialog, failed dispatches |
| 1:37 | Code tour — the composition root, the prioritization policy, the recovery state machine |
| 2:03 | Architecture — one local process, two real channels, SQLite |
| 2:22 | Close — what is guaranteed, and what is not |

The recording is privacy-clean by construction, which is easy to get wrong in a screen recording:

- contacts render as `WhatsApp endpoint` / `Email endpoint` — never a phone number or an address — and the failed-dispatches table masks destinations (`+15••••006`, `e•••••@alderfinch.example`);
- senders render as `Email sender 1` / `WhatsApp sender 1`, so the Senders identity fields never appear on screen;
- the footage comes from `data/demo.db`, seeded by `scripts/seed_demo.py`, and is unrelated to `data/reachout.db`;
- the send dialog is opened and the template rendered so the pre-send review step is visible, but **no message is sent**. The closing card states this on screen.

---

## 🖥️ Dashboard Guide

The SPA is served by FastAPI at the root URL. Navigation is hash-based, so each view has a stable link:

| View | Link | What it does |
| :--- | :--- | :--- |
| **Overview** | `/#/overview` | Coverage KPIs, the campaign run deck (pause/resume), needs-attention queue (failed attempts + recovery), live activity, response outcomes, and channel mix. |
| **Contacts** | `/#/contacts` | Company hierarchy, search and filters across endpoints, CRM outcomes, and follow-ups; send a templated message on either channel. |
| **Senders** | `/#/senders` | WhatsApp QR authentication and health checks, SMTP configuration and live verification, daily/hourly limits. |
| **Templates** | `/#/templates` | Create and edit message templates with variable substitution and attachment paths. |

Canonical domain events stream over `/api/events/ws` (WebSocket), with `/api/events/stream` (SSE) and `/api/events/history` available for clients that prefer them. Sends, failures, campaign state changes, and recovery requirements appear without a refresh.

### Color theme

The button at the bottom of the left rail opens a small menu with **System**, **Light**, and **Dark**. The trigger icon always shows the active mode (monitor / sun / moon). The choice is saved per browser in `localStorage`, and **System** follows your OS setting live.

### Privacy in the interface

The dashboard treats phone numbers and email addresses as personal data. Lists never render raw endpoints:

- Endpoint rows show the channel and a state badge, not the address.
- The send modal labels endpoints as "WhatsApp endpoint 1", "Email endpoint 2", etc. The selected value is sent to the API but is not displayed in lists.
- Failed-attempt and recovery rows show a masked destination (`al+••••@domain.com`, `+91••••123`).
- Sender rows hide account ids and identities; the send modal labels them "WhatsApp sender 1", "Email sender 2".
- The activity timeline and duplicate-contact report omit destinations and shared identifiers.

The full values still exist in the database and are used by the send pipeline. If you need them for debugging, query SQLite directly rather than expecting them in the UI.

---

## 🧪 Demo Database

`scripts/seed_demo.py` builds a **separate** database, `data/demo.db`, with fictional companies, contacts, templates, campaigns, attempts, and follow-up reminders. It copies only the sender-account rows from `data/reachout.db` — never credentials, real contacts, or history — so an already-authenticated environment can be reused safely.

```powershell
# Stop the running app first; the script refuses to write to a locked database.
uv run python scripts/seed_demo.py

$env:DATABASE_URL = "sqlite:///D:/Reachout/data/demo.db"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Switching back to the real database

`DATABASE_URL` is read once at process start. To return to real data, close the server, clear the variable, and start it again:

```powershell
$env:DATABASE_URL = ""
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

To confirm which database is actually in use, compare row counts:

```powershell
.venv/Scripts/python.exe -c "import sqlite3; [print(p, sqlite3.connect(p).execute('select count(*) from companies').fetchone()[0]) for p in ('data/reachout.db','data/demo.db')]"
```

---

## 🏛️ Architecture Overview

Reachout follows clean hexagonal architecture principles with strict layer boundaries:

```text
D:\Reachout\
├── app/
│   ├── domain/               # Pure domain entities, value objects & policies
│   │   ├── campaign.py       # Campaign aggregates & quota tracking
│   │   ├── company.py        # Company domain model
│   │   ├── contact.py        # Contact model & E.164 phone normalization
│   │   ├── enums.py          # Strict type-safe status and channel enums
│   │   ├── message_template.py # Dynamic template model with variable substitution
│   │   ├── outreach_attempt.py # Immutable attempt audit records
│   │   ├── sender_account.py # Sender identity & usage limits
│   │   ├── source_record.py  # Excel source row tracking & SHA-256 fingerprinting
│   │   └── policies/         # Pure domain rules (prioritization, rotation, dedup)
│   ├── ports/                # Abstract protocol interfaces (Hexagonal Ports):
│   │                         # repositories, CampaignScheduler, SourceReader,
│   │                         # SourceSynchronizer, Clock, EventPublisher
│   ├── infrastructure/       # Concrete adapters & external integrations
│   │   ├── database.py       # SQLAlchemy engine & SQLite WAL configuration
│   │   ├── models.py         # Relational database schema models
│   │   ├── events/           # Canonical EventBus & event broadcasting
│   │   ├── providers/        # Camoufox (Firefox) WhatsApp & SMTP Email adapters
│   │   ├── repositories/     # SQLite repository implementations
│   │   ├── scheduler/        # Persistent scheduler & RateLimiter
│   │   ├── security/         # Encrypted credential vault (HMAC-SHA256 + PBKDF2)
│   │   └── source/           # Non-destructive Excel/CSV source reader
│   ├── services/             # Application use case orchestrators (wired via ServiceContext DI:
│   │                         # repositories + Clock + EventPublisher, no concrete imports)
│   ├── api/                  # FastAPI REST and WebSocket routers
│   └── main.py               # Canonical FastAPI application entry point
├── data/                     # Operational databases & source Excel workbooks
├── docs/archive/             # Historical engineering phase reports & audit logs
├── migrations/               # Alembic database migration revisions
├── scripts/                  # Data migration & administrative maintenance utilities
├── tests/                    # Canonical test suite (unit, integration, e2e)
├── ui/                       # Modern SPA CRM dashboard
├── pyproject.toml            # uv / PEP 621 dependencies & project config
└── alembic.ini               # Database migration configuration
```

---

## 🔐 Sender Authentication & Configuration

### WhatsApp Authentication (Camoufox / Firefox)

1. Open the dashboard and go to **Senders**.
2. Under WhatsApp Senders, click **Re-authenticate** on the target sender.
3. Scan the displayed QR code with your mobile device.
4. The background session manager persists authenticated session profiles to `.sessions/` (excluded from Git).

If the browser engine is missing, install it once:

```powershell
uv run python -m camoufox fetch
```

### Email Configuration (SMTP Vault)

1. Go to **Senders** and click **Reconfigure** on an email sender.
2. Provide the SMTP host, port (for example `smtp.gmail.com:587`), username, and app password.
3. Credentials are encrypted at rest using HMAC-SHA256 authenticated encryption via `app.infrastructure.security.credential_vault.CredentialVault`.
4. Click **Test and verify** to perform a live SMTP TLS handshake.

---

## ⚙️ Configuration & Environment Variables

Reachout works with safe defaults out of the box. Everything below is optional.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///data/reachout.db` | Database the API process opens. Set it to run against `data/demo.db` or a copy. |
| `VAULT_ENCRYPTION_KEY` | Auto-generated in `.sessions/.vault_key` | Master key for SMTP credential encryption. |
| `OUTREACH_CHANNEL_DELAY_WA` | `120.0` | Inter-message spacing for WhatsApp, in seconds. |
| `OUTREACH_CHANNEL_DELAY_EM` | `60.0` | Inter-message spacing for Email, in seconds. |
| `REACHOUT_PRIVATE_IDS` | empty | Extra strings to blur in the Gmail capture helper (comma-separated address locals / phone numbers). |

---

## 🛠️ Utility Scripts

| Script | Purpose |
| :--- | :--- |
| `scripts/seed_demo.py` | Rebuild `data/demo.db` with fictional data, reusing sender rows from the real database. |
| `scripts/verify_and_import_good_one.py` | Strict Hunter-based email verification. Runs read-only by default; pass `--apply` to write. |
| `scripts/capture_receiver.py` | Record the receiver side of a demo WhatsApp chat from an authenticated profile (build-side helper). |
| `scripts/capture_gmail_receiver.py` | Record a Gmail inbox proof for the demo video, with automatic address/phone blurring. |

Both capture helpers write under `out/raw/` (ignored by Git) and need `.sessions/`, so they are meant for the machine that owns the authenticated profiles — never for CI or a shared checkout.

---

## 🧪 Running the Tests

### Backend (pytest)

```powershell
uv run pytest
```

Focused runs:

```powershell
# Unit tests
uv run pytest tests/unit/

# Integration tests
uv run pytest tests/integration/

# Browser end-to-end tests (Camoufox / Firefox, ~3 min; tagged `e2e`)
uv run pytest tests/e2e/

# Everything except the browser suite (~45s)
uv run pytest -m "not e2e"
```

> **Run the suite serially.** Several tests drive real threads and sleeps (scheduler pause/resume, server restart, recovery race resolution), and they become timing-sensitive under parallel load. `pytest-xdist` (`-n`) is not supported — a serial run is the authoritative result. A 300s per-test timeout is configured so a hang fails loudly instead of blocking the run.

The E2E suite starts its own server on `127.0.0.1:8899` against an isolated temporary database, so it never touches `data/reachout.db`.

### Frontend unit tests (Node)

```powershell
npm test
```

This runs the `ui/tests/*.test.mjs` suite with Node's built-in test runner. It covers formatting/masking helpers, the action registry, load-state gating, and renderer escaping. No `npm install` is required.

Lint and format the backend with `uv run ruff check app tests` and `uv run ruff format`.

### Live provider tests (sends real messages)

`tests/e2e/live/` dispatches a **real** WhatsApp message and a **real** email. It is skipped unless explicitly opted in, so it never runs from a plain `uv run pytest`.

Sender accounts are discovered from `ACTIVE` rows in `sender_accounts`, and the recipient must be another one of your own accounts (sender ≠ recipient is enforced).

```powershell
# Both channels: needs >= 2 ACTIVE accounts per channel
$env:LIVE_E2E="1"
uv run pytest tests/e2e/live

# Email only
$env:LIVE_EMAIL_E2E="1"
uv run pytest tests/e2e/live/test_email_live.py

# WhatsApp only
$env:LIVE_WHATSAPP_E2E="1"
uv run pytest tests/e2e/live/test_whatsapp_live.py

# Pin a specific sender and recipient when only one account is ACTIVE
$env:LIVE_WHATSAPP_E2E="1"
$env:LIVE_TEST_WHATSAPP_SENDER_ID="wa_919000000000"
$env:LIVE_TEST_WHATSAPP_RECIPIENT="919000000001"
uv run pytest tests/e2e/live/test_whatsapp_live.py

# Clear the opt-in afterwards
$env:LIVE_E2E=""; $env:LIVE_WHATSAPP_E2E=""; $env:LIVE_EMAIL_E2E=""
```

Notes:

- WhatsApp runs **headed** by default. Headless is a strong automation signal on WhatsApp Web, so `PLAYWRIGHT_HEADLESS=true` is an explicit opt-in only.
- WhatsApp must be re-authenticated (QR scan via **Senders**) if the session expires; the test then skips with `ERR_AUTH_REQUIRED` rather than failing.
- Both tests build their fixtures in an in-memory SQLite database, so the production database is never written to — only the outbound message is real.
- Clear `LIVE_*` variables before sharing a shell; the opt-in is env-var based.

---

## 🩺 Troubleshooting

**The dashboard shows data from the wrong database.**
Check the `DATABASE_URL` in the shell that started the server. It is read once at startup; changing it requires a restart. `data/reachout.db` is the default, `data/demo.db` is the synthetic demo.

**A CSS or JS change did not appear after a reload.**
Static asset URLs carry a content hash computed when the server starts (`ASSET_VERSION` in `app/main.py`). Restart the server after editing files under `ui/`, then hard-refresh the page.

**Port 8000 is already in use.**
Stop the old process and start again:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

**The app starts but the Senders page shows nothing.**
Run the server from the project root so `data/` and `.sessions/` resolve correctly, and confirm the database file exists.

**`uv` is not installed.**
Install it once, then use the `uv run ...` commands above:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

If a virtual environment already exists in the checkout, you can bypass `uv` entirely:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 🛡️ Safety Invariants & Policies

1. **Company-First Round-Robin:** Intersperses contacts across distinct companies (Round 1: C1_HR1, C2_HR1, C3_HR1... Round 2: C1_HR2, C2_HR2...) preventing multiple simultaneous contacts at the same company.
2. **Multi-Endpoint HR Coverage:** Dispatches across all available phone numbers and email addresses per contact.
3. **Pacing Compliance:** Authoritative 120s WhatsApp and 60s Email delays enforced by `RateLimiter`.
4. **Crash Recovery:** Atomic `PREPARED -> SENDING -> SENT / FAILED` transitions guarantee orphaned in-flight dispatches are marked `RECOVERY_REQUIRED` following unexpected process termination.
5. **Lossless Source Sync:** Syncing Excel source files never mutates existing database records or historical audit logs.
6. **Operator Recovery:** Stuck attempts are listed via `GET /api/outreach/recovery` and resolved (`mark_sent` / `retry` / `cancel`) from the dashboard Recovery Queue drawer; attachments must resolve via `resolve_attachment_path()` or the send fails loudly instead of degrading to text-only.
