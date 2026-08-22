"""Regression tests for the dashboard-audit fixes.

Covers:
1. Historical send-log CSV reconciliation against the DB (exact counts).
2. Default outreach limit constant (exactly 100, single source of truth).
3. Hierarchy filtering: company / crm_status / priority_filter / channel_status.
4. CSV export endpoint.
"""

from __future__ import annotations

import csv
from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DEFAULT_OUTREACH_LIMIT, MAX_OUTREACH_LIMIT
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, CRMOutcome, InterviewState, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.main import app
from app.services.company_service import CompanyService


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SEND_LOG_CSV = ROOT_DIR / "logs" / "mnc_whatsapp_send_log.csv"
PROD_DB = ROOT_DIR / "data" / "reachout.db"


# ---------------------------------------------------------------------------
# 1. Default limit constant
# ---------------------------------------------------------------------------

def test_default_limit_is_exactly_100():
    """The default outreach limit must be exactly 100, never above."""
    assert DEFAULT_OUTREACH_LIMIT == 100
    assert MAX_OUTREACH_LIMIT >= DEFAULT_OUTREACH_LIMIT


def test_dashboard_html_uses_injected_limit_token_not_hardcoded_200():
    """The UI must source its default limit from the injected token, not a magic 200."""
    html = (ROOT_DIR / "ui" / "crm_dashboard.html").read_text(encoding="utf-8")
    assert "__DEFAULT_OUTREACH_LIMIT__" in html
    assert "__MAX_OUTREACH_LIMIT__" in html
    # Ensure no stale hardcoded 200 fallback remains in the campaign start logic
    assert "|| 200" not in html


# ---------------------------------------------------------------------------
# 2. Historical send-log CSV reconciliation
# ---------------------------------------------------------------------------

def test_historical_send_log_reconciles_with_production_db():
    """CSV -> DB counts must reconcile exactly:
    non-test rows == DB attempts; sent(+text_only) == SENT; failed == FAILED."""
    if not SEND_LOG_CSV.exists() or not PROD_DB.exists():
        pytest.skip("Production send-log / DB not present in this environment")

    with open(SEND_LOG_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    non_test = [r for r in rows if r["source_row"] != "0"]
    csv_sent = sum(1 for r in non_test if r["status"] in ("sent", "sent_text_only"))
    csv_failed = sum(1 for r in non_test if r["status"] == "failed")

    import sqlite3
    conn = sqlite3.connect(PROD_DB)
    db_total = conn.execute("SELECT COUNT(*) FROM outreach_attempts").fetchone()[0]
    db_sent = conn.execute("SELECT COUNT(*) FROM outreach_attempts WHERE status='SENT'").fetchone()[0]
    db_failed = conn.execute("SELECT COUNT(*) FROM outreach_attempts WHERE status='FAILED'").fetchone()[0]
    conn.close()

    assert db_total == len(non_test), f"DB attempts {db_total} != CSV non-test rows {len(non_test)}"
    assert db_sent == csv_sent, f"DB SENT {db_sent} != CSV sent {csv_sent}"
    assert db_failed == csv_failed, f"DB FAILED {db_failed} != CSV failed {csv_failed}"
    assert db_total == db_sent + db_failed


# ---------------------------------------------------------------------------
# Hierarchy filtering fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def hierarchy_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_hierarchy(session):
    comp_repo = SqliteCompanyRepository(session)
    cnt_repo = SqliteContactRepository(session)
    outreach_repo = SqliteOutreachRepository(session)

    # Company A: interested, contacted via WA
    a = Company.create(name="Acme Corp", company_id="acme")
    comp_repo.save(a)
    cnt_repo.save(Contact(
        contact_id="cnt_a1", company_id="acme", name="Alice", phone="919000000001",
        crm_outcome=CRMOutcome.INTERESTED, interview_status=InterviewState.PENDING,
    ))
    att = OutreachAttempt.prepare(
        contact_id="cnt_a1", sender_account_id="WA1", channel=Channel.WHATSAPP,
        attempt_type=AttemptType.AUTOMATIC, message_body="hi", destination="919000000001",
    )
    att.mark_sent("ref-a")
    outreach_repo.save(att)

    # Company B: uncontacted
    b = Company.create(name="Beta Labs", company_id="beta")
    comp_repo.save(b)
    cnt_repo.save(Contact(
        contact_id="cnt_b1", company_id="beta", name="Bob", phone="919000000002",
        crm_outcome=CRMOutcome.NONE,
    ))

    # Company C: not interested
    c = Company.create(name="Gamma Inc", company_id="gamma")
    comp_repo.save(c)
    cnt_repo.save(Contact(
        contact_id="cnt_c1", company_id="gamma", name="Carol", phone="919000000003",
        crm_outcome=CRMOutcome.NOT_INTERESTED,
    ))
    session.commit()


def test_hierarchy_filter_by_company(hierarchy_session_factory):
    SessionFactory = hierarchy_session_factory
    with SessionFactory() as session:
        _seed_hierarchy(session)
    with SessionFactory() as session:
        svc = CompanyService(session)
        names = [h["name"] for h in svc.list_hierarchies(company="acme")]
        assert names == ["Acme Corp"]


def test_hierarchy_filter_by_priority_interested(hierarchy_session_factory):
    SessionFactory = hierarchy_session_factory
    with SessionFactory() as session:
        _seed_hierarchy(session)
    with SessionFactory() as session:
        svc = CompanyService(session)
        names = sorted(h["name"] for h in svc.list_hierarchies(priority_filter="INTERESTED"))
        assert names == ["Acme Corp"]


def test_hierarchy_filter_by_channel_status_not_sent(hierarchy_session_factory):
    SessionFactory = hierarchy_session_factory
    with SessionFactory() as session:
        _seed_hierarchy(session)
    with SessionFactory() as session:
        svc = CompanyService(session)
        names = sorted(h["name"] for h in svc.list_hierarchies(channel_status="NOT_SENT"))
        assert names == ["Beta Labs", "Gamma Inc"]


def test_hierarchy_contact_includes_history_and_followup(hierarchy_session_factory):
    SessionFactory = hierarchy_session_factory
    with SessionFactory() as session:
        _seed_hierarchy(session)
    with SessionFactory() as session:
        svc = CompanyService(session)
        acme = svc.get_company_hierarchy("acme")
        assert acme is not None
        alice = acme["contacts"][0]
        assert "history" in alice
        assert alice["history"][0]["status"] == "SENT"
        assert "follow_up_due" in alice
        assert "reminders" in alice


def _seed_follow_up_due(session, past_days=10, tz=timezone.utc):
    """Seed an interested contact with PENDING interview whose interest is >= threshold days ago."""
    from datetime import timedelta
    comp_repo = SqliteCompanyRepository(session)
    cnt_repo = SqliteContactRepository(session)
    d = Company.create(name="Due Corp", company_id="dueco")
    comp_repo.save(d)
    milestone = datetime.now(tz) - timedelta(days=past_days)
    cnt_repo.save(Contact(
        contact_id="cnt_due1", company_id="dueco", name="Dana",
        phone="919000000099", crm_outcome=CRMOutcome.INTERESTED,
        interview_status=InterviewState.PENDING, interested_at=milestone,
    ))
    session.commit()


def test_hierarchy_follow_up_due_filter(hierarchy_session_factory):
    """Follow-up Due filter must return interested+PENDING contacts past the threshold,
    and correctly handle timezone-aware timestamps."""
    SessionFactory = hierarchy_session_factory
    with SessionFactory() as session:
        _seed_follow_up_due(session, past_days=10)
    with SessionFactory() as session:
        svc = CompanyService(session)
        names = [h["name"] for h in svc.list_hierarchies(priority_filter="FOLLOW_UP_DUE")]
        assert names == ["Due Corp"]

    # timezone handling: milestone may be stored naive (SQLite) but compared to aware now
    with SessionFactory() as session:
        svc = CompanyService(session)
        due = svc.get_company_hierarchy("dueco")
        assert due["contacts"][0]["follow_up_due"] is True


# ---------------------------------------------------------------------------
# CSV export endpoint
# ---------------------------------------------------------------------------

def test_contacts_export_csv_returns_download():
    client = TestClient(app)
    res = client.get("/api/contacts/export/csv")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "reachout_contacts.csv" in res.headers["content-disposition"]
    # Has header row
    lines = res.text.strip().splitlines()
    assert lines
    assert lines[0].startswith("Company,Name")


def test_export_csv_uses_configured_default_limit_in_quick_start_schema():
    """Quick-start must default max_count to the configured constant (not None)."""
    from app.api.campaigns import QuickStartRequest
    assert QuickStartRequest.model_fields["max_count"].default == DEFAULT_OUTREACH_LIMIT
