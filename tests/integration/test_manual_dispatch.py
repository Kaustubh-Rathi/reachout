"""Integration tests for Phase 8 Manual Dispatch, Endpoint Selection, and Safety."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.services.outreach_service import OutreachService


@pytest.fixture
def manual_dispatch_session_factory(tmp_path):
    """Create isolated SQLite database with senders and templates for manual dispatch testing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'manual_dispatch.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        sender_repo = SqliteSenderRepository(session)
        template_repo = SqliteTemplateRepository(session)
        comp_repo = SqliteCompanyRepository(session)
        cnt_repo = SqliteContactRepository(session)

        # Setup Sender
        wa_snd = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+919999900010",
            display_name="WA Dispatcher",
            sender_id="WA_MANUAL_1",
        )
        em_snd = SenderAccount.create(
            channel=Channel.EMAIL,
            provider="smtp_email",
            identity="dispatcher@reachout.io",
            display_name="Email Dispatcher",
            sender_id="EM_MANUAL_1",
        )
        sender_repo.save(wa_snd)
        sender_repo.save(em_snd)

        # Setup Templates
        tpl_wa = MessageTemplate.create(
            channel=Channel.WHATSAPP,
            name="Manual WA Template",
            body="Hello {first_name}, reaching out to {company}.",
            template_id="TPL_MANUAL_WA",
        )
        tpl_em = MessageTemplate.create(
            channel=Channel.EMAIL,
            name="Manual Email Template",
            body="Hi {first_name},\n\nRegarding opportunities at {company}.",
            subject="Job Opportunity - {company}",
            template_id="TPL_MANUAL_EM",
        )
        template_repo.save(tpl_wa)
        template_repo.save(tpl_em)

        # Setup Company and Contact
        comp = Company.create(name="Stripe", company_id="stripe")
        comp_repo.save(comp)

        cnt = Contact(
            contact_id="cnt_stripe_hr1",
            company_id="stripe",
            name="Alex Turner",
            phone="+919876500001, +919876500002",
            email="alex.turner@stripe.com, alex.personal@gmail.com",
        )
        cnt_repo.save(cnt)
        session.commit()

    return SessionFactory


def test_manual_whatsapp_dispatch_records_manual_attempt(manual_dispatch_session_factory):
    """Verify manual WhatsApp dispatch sends to specific destination and records attempt_type=MANUAL."""
    SessionFactory = manual_dispatch_session_factory

    with SessionFactory() as session:
        outreach_svc = OutreachService(session)

        result = outreach_svc.send_whatsapp(
            contact_id="cnt_stripe_hr1",
            custom_body="Custom personalized WhatsApp note",
            destination="919876500002",
            sender_id="WA_MANUAL_1",
            template_id="TPL_MANUAL_WA",
        )

        assert result["success"] is True
        assert result["status"] == "SENT"
        assert result["destination"] == "919876500002"
        assert result["sender_account_id"] == "WA_MANUAL_1"
        assert result["attempt_type"] == "MANUAL"

        session.commit()

    # Verify attempt saved immutably in repository
    with SessionFactory() as session:
        outreach_repo = SqliteOutreachRepository(session)
        attempts = outreach_repo.list_by_contact("cnt_stripe_hr1")
        assert len(attempts) == 1
        att = attempts[0]
        assert att.attempt_type == AttemptType.MANUAL
        assert att.channel == Channel.WHATSAPP
        assert att.destination == "919876500002"
        assert att.status == OutreachStatus.SENT


def test_manual_email_dispatch_records_manual_attempt(manual_dispatch_session_factory):
    """Verify manual Email dispatch sends to specific email endpoint with custom subject and body."""
    SessionFactory = manual_dispatch_session_factory

    with SessionFactory() as session:
        outreach_svc = OutreachService(session)

        result = outreach_svc.send_email(
            contact_id="cnt_stripe_hr1",
            subject="Engineering Inquiry - Stripe",
            custom_body="Custom email body content for Alex",
            destination="alex.turner@stripe.com",
            sender_id="EM_MANUAL_1",
            template_id="TPL_MANUAL_EM",
        )

        assert result["success"] is True
        assert result["status"] == "SENT"
        assert result["destination"] == "alex.turner@stripe.com"
        assert result["sender_account_id"] == "EM_MANUAL_1"
        assert result["attempt_type"] == "MANUAL"

        session.commit()

    # Verify attempt saved
    with SessionFactory() as session:
        outreach_repo = SqliteOutreachRepository(session)
        attempts = outreach_repo.list_by_contact("cnt_stripe_hr1")
        # Should now have both WA and Email attempts
        email_atts = [a for a in attempts if a.channel == Channel.EMAIL]
        assert len(email_atts) == 1
        att = email_atts[0]
        assert att.attempt_type == AttemptType.MANUAL
        assert att.destination == "alex.turner@stripe.com"
        assert att.status == OutreachStatus.SENT
