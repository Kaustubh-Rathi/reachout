"""Integration tests for non-destructive source synchronization and history preservation."""

from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, CRMOutcome, InterviewState, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.services.sync_service import SyncService
from app.services.template_service import TemplateService


@pytest.fixture
def sync_test_session_factory(tmp_path):
    """Create isolated SQLite database for sync preservation test."""
    engine = create_engine(f"sqlite:///{tmp_path / 'sync_preservation.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    # Seed official templates so attempts referencing template_id (FK) resolve.
    with sf() as session:
        TemplateService(session).seed_defaults_if_empty()
        session.commit()
    return sf


def test_excel_sync_preserves_history_timestamps_and_crm_status(sync_test_session_factory, tmp_path):
    """Verify that re-importing source data NEVER deletes or resets outreach history, timestamps, or CRM outcomes."""
    SessionFactory = sync_test_session_factory

    # 1. Seed initial contact with prior history and CRM state
    with SessionFactory() as session:
        comp_repo = SqliteCompanyRepository(session)
        cnt_repo = SqliteContactRepository(session)
        outreach_repo = SqliteOutreachRepository(session)
        sender_repo = SqliteSenderRepository(session)

        # Save Senders for FK constraints
        wa_snd = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+919999900010",
            display_name="WA Dispatcher",
            sender_id="WA1",
        )
        em_snd = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp_email",
            identity="dispatcher@reachout.io",
            display_name="Email Dispatcher",
            sender_id="EMAIL1",
        )
        sender_repo.save(wa_snd)
        sender_repo.save(em_snd)

        comp = Company.create(name="Oracle", company_id="oracle")
        comp_repo.save(comp)

        ts_wa = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        ts_em = datetime(2026, 8, 2, 11, 0, tzinfo=timezone.utc)

        cnt = Contact(
            contact_id="cnt_oracle_hr1",
            company_id="oracle",
            name="Rohit Gupta",
            designation="Talent Acquisition Lead",
            phone="+919810011111",
            email="rohit.gupta@oracle.com",
            last_whatsapp_at=ts_wa,
            last_email_at=ts_em,
            crm_outcome=CRMOutcome.INTERESTED,
            interested_at=ts_wa,
            interview_status=InterviewState.INTERVIEW,
        )
        cnt_repo.save(cnt)

        # Record prior attempts
        att1 = OutreachAttempt(
            id="att_wa_101",
            contact_id="cnt_oracle_hr1",
            campaign_id=None,
            channel=Channel.WHATSAPP,
            sender_account_id="WA1",
            template_id="WA-01",
            destination="919810011111",
            idempotency_key="idemp_wa_101",
            message_body_snapshot="Prior WhatsApp Message",
            attempt_type=AttemptType.AUTOMATIC,
            status=OutreachStatus.SENT,
            provider_reference="msg_wa_ref_999",
            prepared_at=ts_wa,
            completed_at=ts_wa,
        )
        outreach_repo.save(att1)

        att2 = OutreachAttempt(
            id="att_em_202",
            contact_id="cnt_oracle_hr1",
            campaign_id=None,
            channel=Channel.EMAIL,
            sender_account_id="EMAIL1",
            template_id="EMAIL-01",
            destination="rohit.gupta@oracle.com",
            idempotency_key="idemp_em_202",
            message_body_snapshot="Prior Email Message",
            attempt_type=AttemptType.AUTOMATIC,
            status=OutreachStatus.SENT,
            provider_reference="msg_em_ref_888",
            prepared_at=ts_em,
            completed_at=ts_em,
        )
        outreach_repo.save(att2)

        session.commit()

    # 2. Simulate re-importing CSV with an ADDITIONAL phone and email for Rohit Gupta, plus a new HR
    source_file = tmp_path / "sync_update.csv"
    rows = [
        {
            "company": "Oracle",
            "name": "Rohit Gupta",
            "designation": "Director - Talent Acquisition",
            "phone": "+91 98100 11111, +91 98100 22222",  # Added secondary phone
            "email": "rohit.gupta@oracle.com, rohit.alt@oracle.com",  # Added secondary email
        },
        {
            "company": "Oracle",
            "name": "Siddharth Mehta",
            "designation": "HR Manager",
            "phone": "+91 98100 33333",
            "email": "siddharth@oracle.com",
        },
    ]
    with source_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "name", "designation", "phone", "email"])
        writer.writeheader()
        writer.writerows(rows)

    with SessionFactory() as session:
        sync_svc = SyncService(session)
        summary = sync_svc.sync_source(str(source_file))

        # Verify summary metrics
        assert summary["total_read"] == 2
        assert summary["new_contacts"] == 1  # Siddharth Mehta
        assert summary["updated_contacts"] == 1  # Rohit Gupta updated
        assert summary["new_phone_endpoints"] >= 1  # +919810022222
        assert summary["new_email_endpoints"] >= 1  # rohit.alt@oracle.com
        assert summary["history_preserved"] is True

        session.commit()

    # 3. Assert full historical preservation in SQLite database
    with SessionFactory() as session:
        cnt_repo = SqliteContactRepository(session)
        outreach_repo = SqliteOutreachRepository(session)

        # Rohit Gupta contact checks
        rohit = cnt_repo.get_by_id("cnt_oracle_hr1")
        assert rohit is not None
        assert "919810011111" in rohit.phone
        assert "919810022222" in rohit.phone
        assert "rohit.gupta@oracle.com" in rohit.email
        assert "rohit.alt@oracle.com" in rohit.email

        # Critical: Timestamps and CRM states MUST be strictly preserved!
        assert (
            rohit.last_whatsapp_at.replace(tzinfo=timezone.utc)
            if rohit.last_whatsapp_at.tzinfo is None
            else rohit.last_whatsapp_at
        ) == ts_wa
        assert (
            rohit.last_email_at.replace(tzinfo=timezone.utc)
            if rohit.last_email_at.tzinfo is None
            else rohit.last_email_at
        ) == ts_em
        assert rohit.crm_outcome == CRMOutcome.INTERESTED
        assert rohit.interview_status == InterviewState.INTERVIEW
        assert rohit.designation == "Director - Talent Acquisition"

        # Critical: Outreach attempts MUST remain completely intact!
        attempts = outreach_repo.list_by_contact("cnt_oracle_hr1")
        assert len(attempts) == 2
        assert {a.id for a in attempts} == {"att_wa_101", "att_em_202"}
        assert {a.provider_reference for a in attempts} == {"msg_wa_ref_999", "msg_em_ref_888"}
