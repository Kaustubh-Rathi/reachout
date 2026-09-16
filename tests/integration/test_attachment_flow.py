"""Tests for resume/attachment resolution and provider attachment failure semantics."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.providers.attachments import resolve_attachment_path
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider


def test_resolve_attachment_path_is_root_relative_and_absolute():
    resolved = resolve_attachment_path("data/MNC_Final.xlsx")
    assert resolved is not None and resolved.is_absolute() and resolved.exists()
    assert resolve_attachment_path(None) is None
    assert resolve_attachment_path("   ") is None


@pytest.fixture
def attachment_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'attachment.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test resume")

    with factory() as session:
        SqliteCompanyRepository(session).save(Company.create(name="Acme", company_id="acme"))
        SqliteContactRepository(session).save(
            Contact(contact_id="cnt_acme_1", company_id="acme", name="Ada", phone="919900000001")
        )
        SqliteSenderRepository(session).save(
            SenderAccount.create(
                sender_id="WA-ATTACH",
                channel=Channel.WHATSAPP,
                provider="mock",
                identity="+919900000000",
                display_name="Attach Sender",
            )
        )
        SqliteTemplateRepository(session).save(
            MessageTemplate.create(
                template_id="tmpl_attach",
                name="Attach Template",
                channel=Channel.WHATSAPP,
                body="Hello {first_name}",
                attachment_ref=str(resume),
            )
        )
        session.commit()
    return factory, str(resume)


def test_service_passes_template_attachment_to_provider(attachment_session_factory):
    """The template attachment must reach the provider (never silently dropped)."""
    factory, resume_path = attachment_session_factory
    provider = FakeWhatsAppProvider()

    with factory() as session:
        svc = OutreachService(session, whatsapp_provider=provider, email_provider=FakeEmailProvider())
        result = svc.send_whatsapp(contact_id="cnt_acme_1", sender_id="WA-ATTACH", template_id="tmpl_attach")

    assert result["success"] is True
    assert provider.sent_calls[0]["attachment_path"] == resume_path


def test_whatsapp_provider_rejects_missing_attachment(tmp_path):
    """A referenced-but-missing attachment must fail, not send text-only."""
    provider = PlaywrightWhatsAppProvider(session_manager=WhatsAppSessionManager(sessions_root=tmp_path / "sessions"))
    attempt = OutreachAttempt.prepare(
        contact_id="cnt_1",
        sender_account_id="WA-1",
        channel=Channel.WHATSAPP,
        attempt_type=AttemptType.MANUAL,
        message_body="hi",
    )
    result = provider.send_message(
        attempt, recipient_phone="919900000001", message_body="hi", attachment_path="definitely_missing.pdf"
    )
    assert result.success is False
    assert result.failure_code == "ERR_ATTACHMENT_NOT_FOUND"


def test_email_provider_rejects_missing_attachment():
    """SMTP provider must refuse a missing attachment before connecting."""
    provider = SmtpEmailProvider(
        credential_store={"EM-1": {"user": "u@example.com", "password": "p", "host": "smtp.test", "port": "587"}}
    )
    attempt = OutreachAttempt.prepare(
        contact_id="cnt_1",
        sender_account_id="EM-1",
        channel=Channel.EMAIL,
        attempt_type=AttemptType.MANUAL,
        message_body="hi",
    )
    result = provider.send_email(
        attempt, recipient_email="a@b.com", subject="s", message_body="b", attachment_path="definitely_missing.pdf"
    )
    assert result.success is False
    assert result.failure_code == "ERR_ATTACHMENT_NOT_FOUND"
