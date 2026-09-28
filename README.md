# Reachout — Multi-Channel Enterprise Recruitment & Outreach Platform

Reachout is an automated, multi-channel candidate outreach and relationship management (CRM) platform supporting WhatsApp and Email channels. It enforces company-first round-robin scheduling, multi-endpoint HR coverage, provider-compliant rate limiting and pacing, encrypted credential vaults, transactional crash recovery, and real-time WebSocket event streaming.

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
│   │   ├── security/         # Encrypted credential vault (Fernet AES-128-CBC)
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

## 🚀 Quick Start with `uv`

### 1. Prerequisites

Ensure Python 3.10+ and [`uv`](https://github.com/astral-sh/uv) are installed.

```powershell
uv sync
```

### 2. Launch the Production Application

Start the FastAPI application and background worker control plane:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at **[http://localhost:8000](http://localhost:8000)** to access the operational CRM Dashboard.

---

## 🔐 Sender Authentication & Configuration

### WhatsApp Authentication (Camoufox / Firefox)

1. Open the Dashboard at `http://localhost:8000` and navigate to the **Senders** tab.
2. Under WhatsApp Senders, click **Start Authentication** on the target sender account.
3. Scan the displayed WhatsApp QR code with your mobile device.
4. The background session manager persists authenticated session profiles to `.sessions/` (protected from Git).

### Email Configuration (SMTP Vault)

1. Navigate to the **Senders** tab and click **Configure Email Sender**.
2. Provide your SMTP host, port (e.g., `smtp.gmail.com:587`), username, and App Password.
3. Credentials are encrypted at rest using AES-128-CBC via `app.infrastructure.security.vault.CredentialVault`.
4. Click **Verify Connection** to perform live SMTP TLS handshake verification.

---

## ⚙️ Configuration & Environment Variables

Reachout operates with safe defaults out-of-the-box, configurable via environment variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///data/reachout.db` | Primary SQLite database path (WAL mode enabled) |
| `VAULT_ENCRYPTION_KEY` | Auto-generated in `.sessions/.vault_key` | Master key for SMTP credentials encryption |
| `OUTREACH_CHANNEL_DELAY_WA` | `120.0` | Production inter-message spacing delay for WhatsApp (seconds) |
| `OUTREACH_CHANNEL_DELAY_EM` | `60.0` | Production inter-message spacing delay for Email (seconds) |

---

## 🧪 Running the Test Suite

Run the full canonical test suite with pytest:

```powershell
uv run pytest
```

To run specific subsystems:

```powershell
# Unit tests
uv run pytest tests/unit/

# Integration tests
uv run pytest tests/integration/

# End-to-End browser tests (Camoufox / Firefox, ~1 min; tagged `e2e`)
uv run pytest tests/e2e/

# Everything except the browser suite (~45s)
uv run pytest -m "not e2e"
```

> **Run the suite serially.** A number of tests drive real threads and sleeps
> (scheduler pause/resume, server restart, recovery race resolution), and they
> become timing-sensitive under parallel load. `pytest-xdist` (`-n`) is not
> supported here — a serial run is the authoritative result. A 300s per-test
> timeout is configured so a hang fails loudly instead of blocking the run.

The two `tests/e2e/live/` tests dispatch real messages and skip automatically
unless live SMTP / WhatsApp credentials are configured. Lint with
`uv run ruff check app tests` and format with `uv run ruff format`.

### Live Provider Tests (sends real messages)

`tests/e2e/live/` dispatches a **real** WhatsApp message and a **real** email. It is
skipped unless explicitly opted in, so it never runs from a plain `uv run pytest`.

Sender accounts are discovered from `ACTIVE` rows in `sender_accounts`, and the
recipient is another one of your own accounts (sender ≠ recipient is enforced).

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

# Pin a specific sender, and an explicit recipient when only one account is ACTIVE
$env:LIVE_WHATSAPP_E2E="1"
$env:LIVE_TEST_WHATSAPP_SENDER_ID="wa_917875193206"
$env:LIVE_TEST_WHATSAPP_RECIPIENT="917499718082"
uv run pytest tests/e2e/live/test_whatsapp_live.py

# Clear the opt-in afterwards
$env:LIVE_E2E=""; $env:LIVE_WHATSAPP_E2E=""; $env:LIVE_EMAIL_E2E=""
```

Notes:
- WhatsApp runs **headed** by default. Headless is a strong automation signal on
  WhatsApp Web, so `PLAYWRIGHT_HEADLESS=true` is an explicit opt-in only.
- WhatsApp must be re-authenticated (QR scan via the Senders page) if the session
  expires; the test then skips with `ERR_AUTH_REQUIRED` rather than failing.
- Both tests build their fixtures in an in-memory SQLite database, so the
  production database is never written to — only the outbound message is real.
- Clear `LIVE_*` variables before sharing a shell; the opt-in is env-var based.

---

## 🛡️ Safety Invariants & Policies

1. **Company-First Round-Robin:** Intersperses contacts across distinct companies (Round 1: C1_HR1, C2_HR1, C3_HR1... Round 2: C1_HR2, C2_HR2...) preventing multiple simultaneous contacts at the same company.
2. **Multi-Endpoint HR Coverage:** Dispatches across all available phone numbers and email addresses per contact.
3. **Pacing Compliance:** Authoritative 120s WhatsApp and 60s Email delays enforced by `RateLimiter`.
4. **Crash Recovery:** Atomic `PREPARED -> SENDING -> SENT / FAILED` transitions guarantee orphaned in-flight dispatches are marked `RECOVERY_REQUIRED` following unexpected process termination.
5. **Lossless Source Sync:** Syncing Excel source files never mutates existing database records or historical audit logs.
6. **Operator Recovery:** Stuck attempts are listed via `GET /api/outreach/recovery` and resolved (`mark_sent` / `retry` / `cancel`) from the dashboard Recovery Queue drawer; attachments must resolve via `resolve_attachment_path()` or the send fails loudly instead of degrading to text-only.
