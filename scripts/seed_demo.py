"""Seed a synthetic demo database for Reachout.

Creates ``data/demo.db`` with fictional companies, contacts, campaigns,
templates, attempts, and reminders. Sender accounts are copied from the local
``data/reachout.db`` so the demo reuses the already-authenticated WhatsApp and
email identities (no credentials are copied -- only the account rows).

The script forces ``DATABASE_URL`` to the demo database *before* importing
``app.infrastructure.database``: that module binds ``DB_URL`` and
``SessionFactory`` at import time, so setting the variable afterwards would
silently seed the real database instead.

Usage (PowerShell, from D:\\Reachout)::

    uv run python scripts/seed_demo.py

Refuses to run while the demo database is locked by a running app.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEMO_DB = ROOT / "data" / "demo.db"
DEMO_URL = f"sqlite:///{DEMO_DB.as_posix()}"
SOURCE_DB = ROOT / "data" / "reachout.db"

# Must happen before any app import. database.py reads DATABASE_URL at import.
os.environ["DATABASE_URL"] = DEMO_URL

from app.infrastructure.database import SessionFactory, init_db  # noqa: E402
from app.infrastructure.models import (  # noqa: E402
    CampaignModel,
    CompanyModel,
    ContactModel,
    FollowUpReminderModel,
    MessageTemplateModel,
    OutreachAttemptModel,
    SenderAccountModel,
)

NOW = datetime.now(timezone.utc)

COMPANIES = [
    ("cmp_northwind", "Northwind Analytics", "northwind.example", "Data & Analytics"),
    ("cmp_blueharbor", "Blue Harbor Labs", "blueharbor.example", "Product Engineering"),
    ("cmp_meridian", "Meridian Health", "meridianhealth.example", "Healthcare"),
    ("cmp_cobalt", "Cobalt Systems", "cobaltsystems.example", "Cloud Infrastructure"),
    ("cmp_fieldstone", "Fieldstone Retail", "fieldstone.example", "Retail Technology"),
    ("cmp_lumen", "Lumen Finance", "lumenfinance.example", "Fintech"),
    ("cmp_alderfinch", "Alder & Finch", "alderfinch.example", "Design Studio"),
    ("cmp_kestrel", "Kestrel Logistics", "kestrel.example", "Logistics"),
    ("cmp_demo_target", "Demo Target", "demo.example", "Demo"),
]

FIRST_NAMES = [
    "Aarav",
    "Maya",
    "Rohan",
    "Sofia",
    "Daniel",
    "Priya",
    "Ethan",
    "Nina",
    "Kabir",
    "Elena",
    "Marcus",
    "Isha",
    "Leo",
    "Anika",
    "Noah",
    "Tara",
    "Owen",
    "Zara",
    "Felix",
    "Mira",
    "Hugo",
    "Lena",
    "Sam",
    "Ivy",
]
LAST_NAMES = [
    "Shah",
    "Iyer",
    "Mehta",
    "Garcia",
    "Kim",
    "Patel",
    "Brown",
    "Singh",
    "Rao",
    "Petrova",
    "Lee",
    "Nair",
    "Martin",
    "Gupta",
    "Wilson",
    "Khan",
    "Clark",
    "Ali",
    "Muller",
    "Das",
    "Lopez",
    "Fischer",
    "Cole",
    "Bose",
]
DESIGNATIONS = [
    "Talent Acquisition Lead",
    "Engineering Manager",
    "People Operations Partner",
]

DEMO_BODY = (
    "Hi {first_name}, I'm reaching out about the {designation} role at "
    "{company}. We help teams automate candidate outreach without losing the "
    "personal touch. Would you be open to a quick 15-minute call this week?"
)

# 24 contacts: 6 untouched, then a realistic spread of CRM outcomes.
OUTCOMES = (
    ["NONE"] * 6
    + ["CONTACTED"] * 4
    + ["PENDING_REPLY"] * 3
    + ["REPLIED"] * 4
    + ["INTERESTED"] * 3
    + ["INTERVIEW"] * 2
    + ["OFFER"] * 1
    + ["NOT_INTERESTED"] * 1
)

INTERVIEW_STATE = {
    "INTERVIEW": "INTERVIEW",
    "OFFER": "INTERVIEW",
    "INTERESTED": "PENDING",
}


def _remove_demo_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{DEMO_DB}{suffix}")
        if path.exists():
            path.unlink()


def _copy_senders(session) -> int:
    """Copy sender rows (never credentials) from the real DB when present."""
    if not SOURCE_DB.exists():
        print(f"WARNING: {SOURCE_DB} not found; seeding placeholder senders.")
        placeholders = [
            ("WA_SESSION_1", "WHATSAPP", "playwright_whatsapp", "", "WhatsApp Session", "AUTH_REQUIRED"),
            ("WA_SESSION_2", "WHATSAPP", "playwright_whatsapp", "", "WhatsApp Session", "AUTH_REQUIRED"),
            ("EMAIL_SESSION_1", "EMAIL", "smtp", "", "Email Session", "NOT_CONFIGURED"),
            ("EMAIL_SESSION_2", "EMAIL", "smtp", "", "Email Session", "NOT_CONFIGURED"),
        ]
        for sender_id, channel, provider, identity, name, status in placeholders:
            session.add(
                SenderAccountModel(
                    id=sender_id,
                    channel=channel,
                    provider=provider,
                    identity=identity,
                    display_name=name,
                    status=status,
                )
            )
        return len(placeholders)

    source = sqlite3.connect(f"file:{SOURCE_DB.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute(
            "SELECT id, channel, provider, identity, display_name, status, "
            "credential_ref, session_ref, daily_limit, hourly_limit "
            "FROM sender_accounts ORDER BY channel, id"
        ).fetchall()
    finally:
        source.close()

    for row in rows:
        session.add(SenderAccountModel(**dict(row)))
    return len(rows)


def seed() -> None:
    _remove_demo_db()
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    init_db()

    with SessionFactory() as session:
        # --- Senders -------------------------------------------------------
        sender_count = _copy_senders(session)

        # --- Companies -----------------------------------------------------
        for company_id, name, domain, _industry in COMPANIES:
            session.add(
                CompanyModel(
                    id=company_id,
                    name=name,
                    normalized_name=name.strip().lower(),
                    domain=domain,
                )
            )

        # --- Contacts ------------------------------------------------------
        contact_ids: list[str] = []
        contact_models: list[ContactModel] = []
        for index in range(24):
            company_id, company_name, domain, _industry = COMPANIES[index % 8]
            first = FIRST_NAMES[index]
            last = LAST_NAMES[index]
            contact_id = f"ct_demo_{index + 1:02d}"
            contact_ids.append(contact_id)
            outcome = OUTCOMES[index]
            interview = INTERVIEW_STATE.get(outcome, "NOT_APPLICABLE")
            active = outcome not in ("NONE", "CONTACTED")
            contact = ContactModel(
                contact_id=contact_id,
                company_id=company_id,
                name=f"{first} {last}",
                designation=DESIGNATIONS[index % len(DESIGNATIONS)],
                phone=f"+1555010{index + 1:04d}",
                email=f"{first.lower()}.{last.lower()}@{domain}",
                last_whatsapp_at=NOW - timedelta(days=(index % 9) + 1) if active else None,
                last_email_at=NOW - timedelta(days=(index % 7) + 1) if active else None,
                last_activity_at=NOW - timedelta(days=(index % 6) + 1) if active else None,
                crm_outcome=outcome,
                interview_status=interview,
                interested_at=NOW - timedelta(days=3) if outcome in ("INTERESTED", "INTERVIEW", "OFFER") else None,
                notes=f"Demo record for {company_name}.",
            )
            session.add(contact)
            contact_models.append(contact)

        # --- Demo target (receives the real WhatsApp + email) --------------
        session.add(
            ContactModel(
                contact_id="ct_demo_target",
                company_id="cmp_demo_target",
                name="Demo Target",
                designation="Hiring Manager",
                phone="+919000000000",
                email="demo.target@example.com",
                crm_outcome="NONE",
                interview_status="NOT_APPLICABLE",
                notes="Receiver account used for the recorded demo.",
            )
        )

        # --- Templates (same copy on both channels) ------------------------
        session.add_all(
            [
                MessageTemplateModel(
                    id="DEMO-WA-01",
                    name="Demo WhatsApp Outreach",
                    channel="WHATSAPP",
                    body=DEMO_BODY,
                    active=True,
                ),
                MessageTemplateModel(
                    id="DEMO-EMAIL-01",
                    name="Demo Email Outreach",
                    channel="EMAIL",
                    body=DEMO_BODY,
                    subject="Quick question about {company}",
                    active=True,
                ),
            ]
        )

        # --- Campaigns -----------------------------------------------------
        session.add_all(
            [
                CampaignModel(
                    id="camp_demo_wa",
                    name="Demo Outreach - WhatsApp",
                    channel="WHATSAPP",
                    # Startup crash recovery always pauses a RUNNING campaign, so
                    # seed the post-restart state the app will actually show.
                    status="PAUSED",
                    template_ids_json='["DEMO-WA-01"]',
                    sender_account_ids_json='["wa_919000000001"]',
                    started_at=NOW - timedelta(days=5),
                    metadata_json='{"automatic_used": 12, "manual_used": 3, "rotation_state": {}}',
                ),
                CampaignModel(
                    id="camp_demo_email",
                    name="Demo Outreach - Email",
                    channel="EMAIL",
                    status="COMPLETED",
                    template_ids_json='["DEMO-EMAIL-01"]',
                    sender_account_ids_json='["EMAIL_1"]',
                    started_at=NOW - timedelta(days=10),
                    ended_at=NOW - timedelta(days=2),
                    metadata_json='{"automatic_used": 10, "manual_used": 2}',
                ),
            ]
        )

        # --- Attempts ------------------------------------------------------
        session.flush()  # parents must exist before FK rows; autoflush is off
        attempts: list[OutreachAttemptModel] = []
        for index in range(40):
            contact = contact_models[index % 23]
            channel = "WHATSAPP" if index % 2 == 0 else "EMAIL"
            # Only terminal states: startup recovery fails any PREPARED attempt
            # and pauses any RUNNING campaign, so seeding those is pointless.
            status = "SENT" if index % 10 < 6 else "FAILED"

            prepared_at = NOW - timedelta(
                days=index % 14,
                hours=(index * 3) % 24,
                minutes=(index * 7) % 60,
            )
            first_name = contact.name.split()[0]
            body = (
                f"Hi {first_name}, I'm reaching out about the {contact.designation} "
                f"role at {COMPANIES[index % 8][1]}. Would you be open to a quick call?"
            )
            attempts.append(
                OutreachAttemptModel(
                    id=f"att_demo_{index + 1:03d}",
                    contact_id=contact.contact_id,
                    sender_account_id="wa_919000000001" if channel == "WHATSAPP" else "EMAIL_1",
                    channel=channel,
                    attempt_type="MANUAL",
                    status=status,
                    idempotency_key=f"demo-{index + 1:03d}",
                    message_body_snapshot=body,
                    destination=contact.phone if channel == "WHATSAPP" else contact.email,
                    campaign_id="camp_demo_wa" if channel == "WHATSAPP" else "camp_demo_email",
                    template_id="DEMO-WA-01" if channel == "WHATSAPP" else "DEMO-EMAIL-01",
                    subject_snapshot="Quick question" if channel == "EMAIL" else None,
                    prepared_at=prepared_at,
                    started_at=prepared_at + timedelta(minutes=1),
                    completed_at=prepared_at + timedelta(minutes=2),
                    failure_code="ERR_SYNC_TIMEOUT"
                    if status == "FAILED" and channel == "WHATSAPP"
                    else ("ERR_SMTP_TIMEOUT" if status == "FAILED" else None),
                    failure_detail="Demo failure for dashboard coverage." if status == "FAILED" else None,
                )
            )
        session.add_all(attempts)

        # --- Reminders -----------------------------------------------------
        reminder_contacts = contact_ids[13:19]
        for index, contact_id in enumerate(reminder_contacts):
            session.add(
                FollowUpReminderModel(
                    id=f"rem_demo_{index + 1:02d}",
                    contact_id=contact_id,
                    # Three already due (so the KPI is non-zero), three upcoming.
                    due_at=NOW + timedelta(days=index - 2, hours=10),
                    reason="Follow up on the demo conversation",
                    status="PENDING",
                )
            )

        session.commit()

        counts = {
            "companies": session.query(CompanyModel).count(),
            "contacts": session.query(ContactModel).count(),
            "sender_accounts": session.query(SenderAccountModel).count(),
            "message_templates": session.query(MessageTemplateModel).count(),
            "campaigns": session.query(CampaignModel).count(),
            "outreach_attempts": session.query(OutreachAttemptModel).count(),
            "follow_up_reminders": session.query(FollowUpReminderModel).count(),
        }

    print(f"Seeded {DEMO_DB}")
    for table, count in counts.items():
        print(f"  {table:22} {count}")
    print(f"  senders copied         {sender_count}")
    print("  demo target            +919000000000 / demo.target@example.com")

    empty = [table for table, count in counts.items() if count == 0]
    if empty:
        raise SystemExit(f"ERROR: empty tables after seeding: {', '.join(empty)}")


def main() -> int:
    try:
        seed()
    except PermissionError:
        print(
            "ERROR: data/demo.db is locked. Stop the running app before reseeding.",
            file=sys.stderr,
        )
        return 1
    except sqlite3.OperationalError as exc:
        print(f"ERROR: SQLite refused the seed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
