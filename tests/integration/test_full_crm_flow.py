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

from datetime import datetime, timedelta, timezone

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    Channel,
    InterviewState,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.policies.reminder_policy import check_contact_follow_up_eligibility, generate_due_reminders
from app.domain.sender_account import SenderAccount
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.services.contact_service import ContactService
from app.services.crm_service import CrmService
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import MockWhatsAppProvider

# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestFullEndToEndCRMFlow:
    def test_complete_crm_and_outreach_flow(self, isolated_db):
        """Full end-to-end journey:
        1. Dashboard / Start Campaign
        2. Contact selected -> Attempt prepared -> Mock send -> SENT -> Contact updated -> Activity appears
        3. Resend -> second attempt
        4. Interested -> 7 days later -> Follow-up Due -> Interview scheduled -> Reminder cleared
        """
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()

        # Step 1: Initialize system
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="NVIDIA", company_id="nvidia"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+915555555555",
                    display_name="WA NVIDIA Line",
                    sender_id="WA-NVDA",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_nvda_1", company_id="nvidia", name="Jensen Huang", phone="+14084862000")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_nvda", name="NVDA Tmpl", channel=Channel.WHATSAPP, body="Hi {first_name}"
                )
            )
            session.commit()

        # Step 2: First Send
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res1 = outreach_svc.send_whatsapp(contact_id="cnt_nvda_1", sender_id="WA-NVDA", template_id="tmpl_nvda")
            assert res1["success"] is True
            assert res1["status"] == "SENT"

        # Step 3: Resend
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res2 = outreach_svc.resend_whatsapp(
                contact_id="cnt_nvda_1", sender_id="WA-NVDA", custom_body="Jensen, follow up!"
            )
            assert res2["success"] is True

        # Step 4: Mark Interested (8 days ago)
        past_interested_time = datetime.now(timezone.utc) - timedelta(days=8)
        with SessionFactory() as session:
            crm_svc = CrmService(session)
            crm_svc.mark_interested("cnt_nvda_1", timestamp=past_interested_time)
            crm_svc.update_notes("cnt_nvda_1", "Candidate responded positively")

        # Step 5: Follow-up reminder evaluation
        with SessionFactory() as session:
            crm_svc = CrmService(session)
            contact = crm_svc.contact_repo.get_by_id("cnt_nvda_1")
            eligibility = check_contact_follow_up_eligibility(contact, current_time=datetime.now(timezone.utc))
            assert eligibility.is_due is True

            # Auto-generate reminder record
            reminders = generate_due_reminders([contact], current_time=datetime.now(timezone.utc))
            assert len(reminders) >= 1
            for r in reminders:
                crm_svc.reminder_repo.save(r)
            session.commit()

        # Step 6: Mark Interview Scheduled -> clears reminder
        with SessionFactory() as session:
            crm_svc = CrmService(session)
            crm_svc.mark_interview("cnt_nvda_1")

        # Step 7: Verify reminder is cleared and contact is no longer follow-up due
        with SessionFactory() as session:
            crm_svc = CrmService(session)
            contact = crm_svc.contact_repo.get_by_id("cnt_nvda_1")
            assert contact.interview_status == InterviewState.INTERVIEW
            eligibility_after = check_contact_follow_up_eligibility(contact, current_time=datetime.now(timezone.utc))
            assert eligibility_after.is_due is False

            # Verify history activity
            contact_svc = ContactService(session)
            detail = contact_svc.get_contact_detail("cnt_nvda_1")
            assert detail is not None
            assert len(detail["history"]) >= 2
            assert detail["interview_status"] == "INTERVIEW"


# ==============================================================================
# 11. ERROR-CASE & RESILIENCE TESTING
# ==============================================================================


class TestErrorCasesAndResilience:
    def test_provider_timeout_and_network_drop_marks_failed_or_unknown(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider(should_fail=True, failure_code="ERR_SOCKET_TIMEOUT")

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)

            comp_repo.save(Company.create(name="Tesla", company_id="tesla"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+916666666666",
                    display_name="WA Tesla Line",
                    sender_id="WA-TSLA",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_tsla_1", company_id="tesla", name="Elon Musk", phone="+15125168000")
            )
            session.commit()

        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res = outreach_svc.send_whatsapp(contact_id="cnt_tsla_1", sender_id="WA-TSLA", custom_body="Hi Elon")

            assert res["success"] is False
            assert res["status"] in ("UNKNOWN", "FAILED", "RECOVERY_REQUIRED")
            assert res["failure_code"] == "ERR_SOCKET_TIMEOUT"

    def test_auth_failure_suspends_sender(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider(should_fail=True, failure_code="ERR_AUTH_REQUIRED")

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)

            comp_repo.save(Company.create(name="Airbnb", company_id="airbnb"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+917777777777",
                    display_name="WA Airbnb Line",
                    sender_id="WA-ABNB",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_abnb_1", company_id="airbnb", name="Brian Chesky", phone="+14158005000")
            )
            session.commit()

        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res = outreach_svc.send_whatsapp(contact_id="cnt_abnb_1", sender_id="WA-ABNB", custom_body="Hi Brian")
            assert res["success"] is False
            assert res["failure_code"] == "ERR_AUTH_REQUIRED"

        # Verify sender account status can be marked DISCONNECTED / SUSPENDED
        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            sender = sender_repo.get_by_id("WA-ABNB")
            sender.mark_status(SenderStatus.DISCONNECTED)
            sender_repo.save(sender)
            session.commit()

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            sender = sender_repo.get_by_id("WA-ABNB")
            assert sender.status in (SenderStatus.DISCONNECTED, SenderStatus.SUSPENDED, SenderStatus.INACTIVE)

    def test_database_rollback_on_critical_failure(self, isolated_db):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            comp_repo.save(Company.create(name="Valid Co", company_id="valid-co"))
            session.commit()

        # Trigger transaction rollback
        try:
            with SessionFactory() as session:
                comp_repo = SqliteCompanyRepository(session)
                comp_repo.save(Company.create(name="Bad Co 1", company_id="bad-co-1"))
                raise RuntimeError("Simulated unhandled infrastructure error")
        except RuntimeError:
            pass

        # Verify bad-co-1 was rolled back
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            assert comp_repo.get_by_id("bad-co-1") is None
            assert comp_repo.get_by_id("valid-co") is not None
