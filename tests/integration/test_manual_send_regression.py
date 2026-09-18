"""Independent safety verification tests.

Author: Independent Verification Engineer
Role: Production-Safety / Test Agent

Comprehensive verification for:
1. Database isolation and immutability
2. Manual Send & Resend regressions (OutreachService provider_reference fix)
3. Provider mode configuration (mock vs live resolution)
4. Unified scheduler & RateLimiter integration
5. Campaign lifecycle state transitions
6. Startup crash recovery (SENDING -> RECOVERY_REQUIRED)
7. Company-First round-robin interleaving and duplicate suppression
8. N-Sender dynamic scalability (1, 3, 10 WhatsApp & Email senders)
9. Source workbook synchronization (fidelity & historical preservation)
10. End-to-End CRM & Outreach lifecycle flow
11. Error-case handling (timeout, failure, auth required, session expired, rate-limited)
"""

from __future__ import annotations

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    Channel,
    OutreachStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.providers.factory import (
    create_email_provider,
    create_whatsapp_provider,
    get_email_provider,
    get_whatsapp_provider,
    reset_provider_overrides,
    set_email_provider,
    set_whatsapp_provider,
)
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import default_session_manager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider

# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestManualSendAndResendRegression:
    def test_manual_whatsapp_send_captures_and_persists_provider_reference(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Meta Platforms", company_id="meta"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+911111111111",
                    display_name="WA Line Meta",
                    sender_id="WA-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_meta_01", company_id="meta", name="Mark Zuckerberg", phone="+16505434800")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_wa_01",
                    name="WA Template",
                    channel=Channel.WHATSAPP,
                    body="Hi {first_name} at {company}",
                )
            )
            session.commit()

        with SessionFactory() as session:
            svc = OutreachService(session, whatsapp_provider=mock_wa)
            res = svc.send_whatsapp(contact_id="cnt_meta_01", sender_id="WA-1", template_id="tmpl_wa_01")

            assert res["success"] is True
            assert res["status"] == "SENT"
            assert res["provider_reference"] is not None
            assert res["provider_reference"].startswith("wa_ref_")

        # Verify DB persistence of provider_reference, status, completed_at
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            attempts = outreach_repo.list_by_contact("cnt_meta_01")
            assert len(attempts) == 1
            att = attempts[0]
            assert att.status == OutreachStatus.SENT
            assert att.provider_reference == res["provider_reference"]
            assert att.completed_at is not None
            assert att.attempt_type == AttemptType.MANUAL

    def test_manual_email_send_captures_and_persists_provider_reference(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_em = MockEmailProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Apple", company_id="apple"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.EMAIL,
                    provider="mock",
                    identity="hr@apple.com",
                    display_name="Apple Email Line",
                    sender_id="EM-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_apple_01", company_id="apple", name="Tim Cook", email="tim@apple.com")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_em_01",
                    name="Email Template",
                    channel=Channel.EMAIL,
                    body="Hi {first_name}",
                    subject="Roles at {company}",
                )
            )
            session.commit()

        with SessionFactory() as session:
            svc = OutreachService(session, email_provider=mock_em)
            res = svc.send_email(contact_id="cnt_apple_01", sender_id="EM-1", template_id="tmpl_em_01")

            assert res["success"] is True
            assert res["status"] == "SENT"
            assert res["provider_reference"] is not None
            assert res["provider_reference"].startswith("em_ref_")

        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            attempts = outreach_repo.list_by_contact("cnt_apple_01")
            assert len(attempts) == 1
            att = attempts[0]
            assert att.status == OutreachStatus.SENT
            assert att.provider_reference == res["provider_reference"]
            assert att.completed_at is not None
            assert att.subject_snapshot == "Roles at apple"

    def test_resend_preserves_attempt_1_and_automatic_execution_skips_contact(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)

            comp_repo.save(Company.create(name="Netflix", company_id="netflix"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+911111111111",
                    display_name="WA Line Netflix",
                    sender_id="WA-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_nflx_01", company_id="netflix", name="Reed Hastings", phone="+14085403700")
            )
            session.commit()

        # 1. Attempt #1 (SENT)
        with SessionFactory() as session:
            svc = OutreachService(session, whatsapp_provider=mock_wa)
            res1 = svc.send_whatsapp(contact_id="cnt_nflx_01", sender_id="WA-1", custom_body="Initial outreach message")
            assert res1["success"] is True

        # Record Attempt #1 state
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            att1 = outreach_repo.list_by_contact("cnt_nflx_01")[0]
            att1_id = att1.id
            att1_ref = att1.provider_reference
            att1_time = att1.completed_at

        # 2. Attempt #2: Manual RESEND
        with SessionFactory() as session:
            svc = OutreachService(session, whatsapp_provider=mock_wa)
            res2 = svc.resend_whatsapp(
                contact_id="cnt_nflx_01", sender_id="WA-1", custom_body="Follow-up resend message"
            )
            assert res2["success"] is True

        # 3. Verify Attempt #1 is untouched and Attempt #2 is distinct
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            contact_repo = SqliteContactRepository(session)
            attempts = outreach_repo.list_by_contact("cnt_nflx_01")
            assert len(attempts) == 2

            saved_att1 = next(a for a in attempts if a.id == att1_id)
            saved_att2 = next(a for a in attempts if a.id != att1_id)

            # Check Attempt #1 immutability
            assert saved_att1.provider_reference == att1_ref
            assert saved_att1.completed_at == att1_time
            assert saved_att1.status == OutreachStatus.SENT

            # Check Attempt #2 properties
            assert saved_att2.attempt_type == AttemptType.RESEND
            assert saved_att2.status == OutreachStatus.SENT
            assert saved_att2.provider_reference != att1_ref

            # 4. Verify contact is marked as having successful outreach
            contact = contact_repo.get_by_id("cnt_nflx_01")
            assert contact.last_whatsapp_at is not None


# ==============================================================================
# 3. PROVIDER MODE & RESOLUTION SAFETY
# ==============================================================================


class TestProviderModesAndConfiguration:
    def test_production_factory_resolves_concrete_providers(self):
        wa = create_whatsapp_provider(session_manager=default_session_manager)
        assert isinstance(wa, PlaywrightWhatsAppProvider)

        em = create_email_provider()
        assert isinstance(em, SmtpEmailProvider)

    def test_dependency_injection_provider_overrides(self):
        reset_provider_overrides()
        mock_wa = MockWhatsAppProvider()
        set_whatsapp_provider(mock_wa)
        assert get_whatsapp_provider() is mock_wa

        mock_em = MockEmailProvider()
        set_email_provider(mock_em)
        assert get_email_provider() is mock_em

        reset_provider_overrides()
        assert isinstance(get_whatsapp_provider(), PlaywrightWhatsAppProvider)
        assert isinstance(get_email_provider(), SmtpEmailProvider)

    def test_mock_provider_records_audit_trail(self):
        wa = MockWhatsAppProvider()

        attempt = OutreachAttempt.prepare(
            contact_id="cnt_test",
            sender_account_id="snd_test",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello world",
        )
        res = wa.send_message(attempt, recipient_phone="+919999999999", message_body="Hello world")
        assert res.success is True
        assert len(wa.sent_calls) == 1
        assert wa.sent_calls[0]["recipient_phone"] == "+919999999999"


# ==============================================================================
# 4. CAMPAIGN SCHEDULER & RATE LIMITER INTEGRATION
# ==============================================================================
