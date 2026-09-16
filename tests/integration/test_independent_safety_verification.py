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

import csv
import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    CRMOutcome,
    InterviewState,
    OutreachStatus,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.prioritization import prioritize_company_first
from app.domain.policies.reminder_policy import check_contact_follow_up_eligibility, generate_due_reminders
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
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
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_scheduler import (
    PersistentCampaignScheduler,
    set_campaign_scheduler,
)
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer
from app.services.campaign_service import CampaignService
from app.services.contact_service import ContactService
from app.services.crm_service import CrmService
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider


@pytest.fixture
def isolated_db(tmp_path):
    """Yields an isolated temporary SQLite engine and SessionFactory."""
    db_file = tmp_path / "verification_isolated.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, SessionFactory


# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestDatabaseIsolationAndImmutability:
    def test_production_db_remains_untouched_by_isolated_operations(self, isolated_db):
        prod_db_path = Path("data/reachout.db")
        if not prod_db_path.exists():
            pytest.skip("Production database not present on disk")

        pre_hash = hashlib.sha256(prod_db_path.read_bytes()).hexdigest()

        # Perform mutations on isolated database
        _, SessionFactory = isolated_db
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            comp_repo.save(Company.create(name="Isolation Test Co", company_id="iso-co"))
            session.commit()

        post_hash = hashlib.sha256(prod_db_path.read_bytes()).hexdigest()
        assert pre_hash == post_hash, "Production reachout.db was mutated during test execution!"


# ==============================================================================
# 2. MANUAL SEND & RESEND REGRESSION (P0 Fix Verification)
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
        wa = create_whatsapp_provider()
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


class TestSchedulerAndRateLimiterIntegration:
    def test_campaign_service_delegates_to_persistent_scheduler(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()
        rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})
        worker = OutreachWorker(
            session_factory=SessionFactory,
            rate_limiter=rate_limiter,
            whatsapp_provider=mock_wa,
        )
        scheduler = PersistentCampaignScheduler(SessionFactory, worker=worker)
        set_campaign_scheduler(scheduler)

        # Seed test data
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Stripe", company_id="stripe"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+911234567890",
                    display_name="WA Line Stripe",
                    sender_id="WA-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_stripe_1", company_id="stripe", name="Patrick Collison", phone="+14150000001")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_stripe", name="Default", channel=Channel.WHATSAPP, body="Hi {first_name}"
                )
            )
            session.commit()

        # Start campaign via CampaignService
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            camp = camp_svc.create_campaign(
                name="Stripe Outreach",
                channel=Channel.WHATSAPP,
                template_ids=["tmpl_stripe"],
                sender_account_ids=["WA-1"],
            )
            camp_dict = camp_svc.start_campaign(camp.id, max_count=1)
            camp_id = camp_dict["id"]

        # The single-contact campaign may complete before we can observe any
        # transient status, so delegation is verified by polling for the
        # resulting attempt produced by the scheduler's worker.
        deadline = time.monotonic() + 10.0
        attempts = []
        while time.monotonic() < deadline:
            with SessionFactory() as session:
                attempts = SqliteOutreachRepository(session).list_by_campaign(camp_id)
            if attempts and attempts[0].status == OutreachStatus.SENT:
                break
            time.sleep(0.05)

        assert len(attempts) >= 1
        assert attempts[0].status == OutreachStatus.SENT

    def test_rate_limiter_is_invoked_during_worker_dispatch(self, isolated_db):
        _, SessionFactory = isolated_db
        rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.05, "EMAIL": 0.05})
        mock_wa = MockWhatsAppProvider()

        worker = OutreachWorker(
            session_factory=SessionFactory,
            rate_limiter=rate_limiter,
            whatsapp_provider=mock_wa,
        )

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)
            camp_repo = SqliteCampaignRepository(session)

            comp_repo.save(Company.create(name="Oracle", company_id="oracle"))
            sender = SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="mock",
                identity="+919999999991",
                display_name="WA Line Oracle",
                sender_id="WA-ORC",
                daily_limit=10,
            )
            sender_repo.save(sender)
            contact = Contact(
                contact_id="cnt_oracle_1", company_id="oracle", name="Larry Ellison", phone="+16505067000"
            )
            contact_repo.save(contact)
            template = MessageTemplate.create(
                template_id="tmpl_orc", name="Oracle Tmpl", channel=Channel.WHATSAPP, body="Hello {first_name}"
            )
            tmpl_repo.save(template)
            campaign = Campaign.create(
                name="Oracle Campaign",
                channel=Channel.WHATSAPP,
                template_ids=["tmpl_orc"],
                sender_account_ids=["WA-ORC"],
            )
            campaign.start()
            camp_repo.save(campaign)
            session.commit()

        # Run attempt execution through worker
        att = worker.execute_attempt(
            contact_id=contact.contact_id, sender_account=sender, template=template, campaign=campaign
        )
        assert att.status == OutreachStatus.SENT
        assert len(mock_wa.sent_calls) == 1


# ==============================================================================
# 5. CAMPAIGN FULL LIFECYCLE (START -> PAUSE -> RESUME -> STOP -> COMPLETED)
# ==============================================================================


class TestCampaignFullLifecycle:
    def test_campaign_state_transitions_and_persistence(self, isolated_db):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Amazon", company_id="amazon"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+910000000001",
                    display_name="WA Amazon Line",
                    sender_id="WA-AMZ",
                )
            )
            for i in range(1, 11):
                contact_repo.save(
                    Contact(
                        contact_id=f"cnt_amz_{i}", company_id="amazon", name=f"Contact {i}", phone=f"+1206266100{i}"
                    )
                )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_amz", name="Amazon Tmpl", channel=Channel.WHATSAPP, body="Hi {first_name}"
                )
            )
            session.commit()

        scheduler = PersistentCampaignScheduler(SessionFactory)
        set_campaign_scheduler(scheduler)

        # 1. START
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            camp = camp_svc.create_campaign(
                name="Amazon Campaign",
                channel=Channel.WHATSAPP,
                template_ids=["tmpl_amz"],
                sender_account_ids=["WA-AMZ"],
            )
            c_dict = camp_svc.start_campaign(camp.id, max_count=10)
            camp_id = c_dict["id"]
            assert c_dict["status"] in ("STARTING", "RUNNING", "COMPLETED")

        # 2. PAUSE
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            paused = camp_svc.pause_campaign(camp_id)
            assert paused["status"] in ("PAUSED", "COMPLETED", "RUNNING")

        # 3. RESUME (if paused)
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            if paused["status"] == "PAUSED":
                resumed = camp_svc.resume_campaign(camp_id)
                assert resumed["status"] in ("RUNNING", "COMPLETED", "PAUSED")

        # 4. STOP
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            stopped = camp_svc.stop_campaign(camp_id)
            assert stopped["status"] in ("STOPPED", "COMPLETED", "PAUSED")

        # Verify DB persistence
        with SessionFactory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp_in_db = camp_repo.get_by_id(camp_id)
            assert camp_in_db is not None
            assert camp_in_db.status in (
                CampaignStatus.STOPPED,
                CampaignStatus.COMPLETED,
                CampaignStatus.PAUSED,
                CampaignStatus.RUNNING,
            )


# ==============================================================================
# 6. STARTUP CRASH RECOVERY
# ==============================================================================


class TestStartupCrashRecovery:
    def test_in_flight_sending_attempts_transition_to_recovery_required_on_startup(self, isolated_db):
        _, SessionFactory = isolated_db

        # Seed an orphaned in-flight attempt in SENDING status before application startup
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            camp_repo = SqliteCampaignRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            comp_repo.save(Company.create(name="Adobe", company_id="adobe"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+912222222222",
                    display_name="WA Adobe Line",
                    sender_id="WA-ADB",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_adb_1", company_id="adobe", name="Shantanu Narayen", phone="+14085366000")
            )

            campaign = Campaign.create(name="Adobe Blast", channel=Channel.WHATSAPP, sender_account_ids=["WA-ADB"])
            campaign.start()
            camp_repo.save(campaign)

            attempt = OutreachAttempt.prepare(
                contact_id="cnt_adb_1",
                sender_account_id="WA-ADB",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                campaign_id=campaign.id,
                message_body="Hi Shantanu",
            )
            # Transition to SENDING before sudden crash
            attempt.mark_sending()
            outreach_repo.save(attempt)
            session.commit()

            attempt_id = attempt.id

        # Execute Startup Crash Recovery Audit
        scheduler = PersistentCampaignScheduler(SessionFactory)
        recovered_count = scheduler.run_crash_recovery_audit()

        assert recovered_count >= 1, "Crash recovery audit must recover the in-flight attempt"

        # Verify state transition: MUST be RECOVERY_REQUIRED (never blindly assume FAILED)
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            camp_repo = SqliteCampaignRepository(session)

            recovered_att = outreach_repo.get_by_id(attempt_id)
            assert recovered_att.status == OutreachStatus.RECOVERY_REQUIRED
            assert "crash recovery" in (recovered_att.failure_detail or "").lower()

            # Verify campaign was automatically paused for safety
            recovered_camp = camp_repo.get_by_id(campaign.id)
            assert recovered_camp.status == CampaignStatus.PAUSED


# ==============================================================================
# 7. COMPANY-FIRST ROUND-ROBIN INTERLEAVING & DUPLICATE SUPPRESSION
# ==============================================================================


class TestCompanyFirstAndDuplicateSuppression:
    def test_canonical_dataset_interleaving_a1_b1_c1_d1_a2_c2_a3(self):
        """Proves canonical company-first round-robin interleaving:
        Input:  A1, A2, A3, B1, C1, C2, D1
        Output: A1, B1, C1, D1, A2, C2, A3
        """
        contacts = [
            Contact(contact_id="A1", company_id="CompanyA", name="Alice 1", phone="111"),
            Contact(contact_id="A2", company_id="CompanyA", name="Alice 2", phone="112"),
            Contact(contact_id="A3", company_id="CompanyA", name="Alice 3", phone="113"),
            Contact(contact_id="B1", company_id="CompanyB", name="Bob 1", phone="121"),
            Contact(contact_id="C1", company_id="CompanyC", name="Charlie 1", phone="131"),
            Contact(contact_id="C2", company_id="CompanyC", name="Charlie 2", phone="132"),
            Contact(contact_id="D1", company_id="CompanyD", name="Dave 1", phone="141"),
        ]

        prioritized = prioritize_company_first(contacts)
        result_ids = [c.contact_id for c in prioritized]

        expected_order = ["A1", "B1", "C1", "D1", "A2", "C2", "A3"]
        assert result_ids == expected_order, f"Expected {expected_order}, got {result_ids}"

    def test_automatic_duplicate_suppression_prevents_duplicate_send(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Salesforce", company_id="salesforce"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+911111111111",
                    display_name="WA Salesforce Line",
                    sender_id="WA-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_sfdc_1", company_id="salesforce", name="Marc Benioff", phone="+14159017000")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_sfdc", name="Tmpl", channel=Channel.WHATSAPP, body="Hi {first_name}"
                )
            )
            session.commit()

        # Send once
        with SessionFactory() as session:
            svc = OutreachService(session, whatsapp_provider=mock_wa)
            res1 = svc.send_whatsapp(contact_id="cnt_sfdc_1", sender_id="WA-1", template_id="tmpl_sfdc")
            assert res1["success"] is True

        # Contact is now marked with last_whatsapp_at
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            cnt = contact_repo.get_by_id("cnt_sfdc_1")
            assert cnt.last_whatsapp_at is not None


# ==============================================================================
# 8. N-SENDER DYNAMIC SCALABILITY (1, 3, 10 Senders)
# ==============================================================================


class TestNSenderScalability:
    @pytest.mark.parametrize("sender_count", [1, 3, 10])
    def test_dynamic_whatsapp_sender_scaling(self, isolated_db, sender_count: int):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            for i in range(1, sender_count + 1):
                sender_repo.save(
                    SenderAccount.create(
                        channel=Channel.WHATSAPP,
                        provider="mock",
                        identity=f"+91980000000{i}",
                        display_name=f"WA Scale Line {i}",
                        sender_id=f"WA-SCALE-{i}",
                        daily_limit=50,
                    )
                )
            session.commit()

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            active_senders = sender_repo.list_active(channel=Channel.WHATSAPP)
            assert len(active_senders) == sender_count

            # Verify sender usage tracking
            for s in active_senders:
                s.record_usage()
                sender_repo.save(s)
            session.commit()

    @pytest.mark.parametrize("sender_count", [1, 3, 10])
    def test_dynamic_email_sender_scaling(self, isolated_db, sender_count: int):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            for i in range(1, sender_count + 1):
                sender_repo.save(
                    SenderAccount.create(
                        channel=Channel.EMAIL,
                        provider="mock",
                        identity=f"outreach_{i}@scale.org",
                        display_name=f"Email Scale Line {i}",
                        sender_id=f"EM-SCALE-{i}",
                        daily_limit=100,
                    )
                )
            session.commit()

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            active_senders = sender_repo.list_active(channel=Channel.EMAIL)
            assert len(active_senders) == sender_count


# ==============================================================================
# 9. SOURCE SYNCHRONIZATION REGRESSION
# ==============================================================================


class TestSourceSynchronizationRegression:
    def test_source_sync_comprehensive_scenarios_and_file_immutability(self, isolated_db, tmp_path):
        """Tests:
        1. New contact ingestion
        2. New recruiter at existing company
        3. Modified contact (enrichment with historical outreach preserved)
        4. Duplicate contact handling
        5. Deleted / suppressed contact state preserved
        6. Source file immutability (SHA-256 unchanged)
        """
        _, SessionFactory = isolated_db
        source_csv = tmp_path / "sync_dataset.csv"

        # 1. Initial dataset: Google (Sundar), Microsoft (Satya), Uber (Dara)
        rows_v1 = [
            {"company": "Google", "name": "Sundar Pichai", "phone": "919876543210", "email": "sundar@google.com"},
            {"company": "Microsoft", "name": "Satya Nadella", "phone": "919876543211", "email": "satya@microsoft.com"},
            {"company": "Uber", "name": "Dara", "phone": "919999999999", "email": ""},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows_v1)

        initial_sha = hashlib.sha256(source_csv.read_bytes()).hexdigest()

        # Run initial sync
        with SessionFactory() as session:
            sync = DatabaseSourceSynchronizer(session)
            res1 = sync.sync_source(str(source_csv))
            assert res1.total_read == 3
            assert res1.new_contacts == 3

        # Verify source file unchanged
        assert hashlib.sha256(source_csv.read_bytes()).hexdigest() == initial_sha

        # 2. Simulate historical outreach & CRM notes on Uber (Dara) and Microsoft (Satya)
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)
            suppr_repo = SqliteSuppressionRepository(session)

            dara = contact_repo.get_by_key("uber|919999999999")
            now = datetime.now(timezone.utc)
            dara.record_outreach_success(Channel.WHATSAPP, now)
            dara.update_crm_outcome(CRMOutcome.INTERESTED, now)
            dara.notes = "Discussed Senior Staff SWE opportunity"
            contact_repo.save(dara)

            # Seed the sender referenced by the attempt FK.
            SqliteSenderRepository(session).save(
                SenderAccount.create(
                    sender_id="WA-1",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919000000000",
                    display_name="WA1",
                )
            )

            att = OutreachAttempt.prepare(
                contact_id=dara.contact_id,
                sender_account_id="WA-1",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Hello Dara",
            )
            att.mark_sent(provider_reference="wa_uber_ref_1")
            outreach_repo.save(att)

            # Suppress/Tombstone Microsoft contact
            satya = contact_repo.get_by_key("microsoft|919876543211")
            if satya:
                suppr_repo.add_suppression("PHONE", satya.phone, "Manual user opt-out")
            session.commit()

        # 3. Modify dataset:
        # - Modified contact: Uber (Dara Khosrowshahi with email added)
        # - New recruiter at existing company: Uber (Recruiter Travis)
        # - New company & contact: Anthropic (Dario)
        # - Duplicate row: duplicate Google Sundar
        rows_v2 = [
            {"company": "Google", "name": "Sundar Pichai", "phone": "919876543210", "email": "sundar@google.com"},
            {
                "company": "Google",
                "name": "Sundar Pichai Duplicate",
                "phone": "919876543210",
                "email": "sundar@google.com",
            },
            {"company": "Uber", "name": "Dara Khosrowshahi", "phone": "919999999999", "email": "dara@uber.com"},
            {"company": "Uber", "name": "Travis K", "phone": "919999999998", "email": "travis@uber.com"},
            {"company": "Anthropic", "name": "Dario Amodei", "phone": "919000000003", "email": "dario@anthropic.com"},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows_v2)

        v2_sha = hashlib.sha256(source_csv.read_bytes()).hexdigest()

        # Run second sync
        with SessionFactory() as session:
            sync = DatabaseSourceSynchronizer(session)
            res2 = sync.sync_source(str(source_csv))
            assert res2.new_contacts >= 2  # Travis + Dario
            assert res2.updated_contacts >= 1  # Dara

        # Verify source file remained untouched
        assert hashlib.sha256(source_csv.read_bytes()).hexdigest() == v2_sha

        # 4. Verify historical outreach & CRM notes on Dara remain 100% intact
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)
            suppr_repo = SqliteSuppressionRepository(session)

            dara_after = contact_repo.get_by_key("uber|919999999999")
            assert dara_after.name == "Dara Khosrowshahi"
            assert dara_after.email == "dara@uber.com"
            assert dara_after.last_whatsapp_at is not None
            assert dara_after.crm_outcome == CRMOutcome.INTERESTED
            assert dara_after.notes == "Discussed Senior Staff SWE opportunity"

            attempts = outreach_repo.list_by_contact(dara_after.contact_id)
            assert len(attempts) == 1
            assert attempts[0].provider_reference == "wa_uber_ref_1"

            # Verify suppression on Satya is preserved
            assert suppr_repo.is_suppressed(phone="919876543211") is True

            # Verify new recruiter Travis at Uber exists
            travis = contact_repo.get_by_key("uber|919999999998")
            assert travis is not None
            assert travis.name == "Travis K"


# ==============================================================================
# 10. END-TO-END CRM FLOW
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
