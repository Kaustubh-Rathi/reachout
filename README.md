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
│   ├── ports/                # Abstract protocol interfaces (Hexagonal Ports)
│   ├── infrastructure/       # Concrete adapters & external integrations
│   │   ├── database.py       # SQLAlchemy engine & SQLite WAL configuration
│   │   ├── models.py         # Relational database schema models
│   │   ├── events/           # Canonical EventBus & event broadcasting
│   │   ├── providers/        # Playwright WhatsApp & SMTP Email adapters
│   │   ├── repositories/     # SQLite repository implementations
│   │   ├── scheduler/        # Persistent scheduler & RateLimiter
│   │   ├── security/         # Encrypted credential vault (Fernet AES-128-CBC)
│   │   └── source/           # Non-destructive Excel/CSV source reader
│   ├── services/             # Application use case orchestrators
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

### WhatsApp Authentication (Playwright Chromium)

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

# End-to-End browser tests (requires Playwright Chromium)
uv run pytest tests/e2e/
```

---

## 🛡️ Safety Invariants & Policies

1. **Company-First Round-Robin:** Intersperses contacts across distinct companies (Round 1: C1_HR1, C2_HR1, C3_HR1... Round 2: C1_HR2, C2_HR2...) preventing multiple simultaneous contacts at the same company.
2. **Multi-Endpoint HR Coverage:** Dispatches across all available phone numbers and email addresses per contact.
3. **Pacing Compliance:** Authoritative 120s WhatsApp and 60s Email delays enforced by `RateLimiter`.
4. **Crash Recovery:** Atomic `PREPARED -> SENDING -> SENT / FAILED` transitions guarantee orphaned in-flight dispatches are marked `RECOVERY_REQUIRED` following unexpected process termination.
5. **Lossless Source Sync:** Syncing Excel source files never mutates existing database records or historical audit logs.
