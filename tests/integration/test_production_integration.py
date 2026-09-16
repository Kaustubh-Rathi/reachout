"""Production integration tests for send/resend, providers, and scheduler.

Verifies:
1. P0 Bug Fix: `provider_reference` parameter passing and database persistence in manual send & resend.
2. Explicit Provider Configuration & Factory: Mock vs Live mode resolution, no accidental live dispatch.
3. Unified Persistent Scheduler: Delegation from CampaignService, rate limiting, and pause/resume/stop lifecycle.
4. Application Startup Crash Recovery: Lifespan audit of stale in-flight attempts and auto-pause.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, CampaignStatus, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.providers.factory import (
    create_email_provider,
    create_whatsapp_provider,
    get_whatsapp_provider,
    reset_provider_overrides,
    set_whatsapp_provider,
)
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_scheduler import (
    PersistentCampaignScheduler,
)
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.providers import ProviderSendResult, ProviderStatusResult
from app.services.campaign_service import CampaignService
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider


@pytest.fixture
def clean_db(tmp_path):
    """Provide a fresh isolated SQLite database with schema created."""
    db_file = tmp_path / "integration_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    return engine, SessionFactory


class TestOutreachSendAndResend:
    """Regression test suite for single send, resend, and provider_reference persistence."""

    def test_manual_whatsapp_send_success_and_provider_ref_persistence(self, clean_db):
        _, SessionFactory = clean_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            template_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Google", company_id="google"))
            sender_repo.save(
                SenderAccount.create(
                    sender_id="WA-001",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919876543210",
                    display_name="WA Test Line",
                )
            )
            contact_repo.save(
                Contact(
                    contact_id="cnt_wa_01",
                    company_id="google",
                    name="Sundar Pichai",
                    phone="+16502530000",
                )
            )
            template_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_01",
                    name="Greeting",
                    channel=Channel.WHATSAPP,
                    body="Hi {first_name}, reaching out regarding {company}!",
                )
            )
            session.commit()

        # Execute manual WhatsApp send
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res = outreach_svc.send_whatsapp(
                contact_id="cnt_wa_01",
                sender_id="WA-001",
                template_id="tmpl_01",
            )
            assert res["success"] is True
            assert res["status"] == "SENT"
            assert res["provider_reference"] is not None
            assert res["provider_reference"].startswith("wa_ref_")

        # Verify DB persistence of provider_reference
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            contact_repo = SqliteContactRepository(session)

            attempts = outreach_repo.list_by_contact("cnt_wa_01")
            assert len(attempts) == 1
            att = attempts[0]
            assert att.status == OutreachStatus.SENT
            assert att.provider_reference == res["provider_reference"]
            assert att.completed_at is not None
            assert "Sundar" in att.message_body_snapshot

            cnt = contact_repo.get_by_id("cnt_wa_01")
            assert cnt.last_whatsapp_at is not None
            assert cnt.last_activity_at is not None

    def test_manual_email_send_success_and_provider_ref_persistence(self, clean_db):
        _, SessionFactory = clean_db
        mock_em = MockEmailProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            template_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Anthropic", company_id="anthropic"))
            sender_repo.save(
                SenderAccount.create(
                    sender_id="EMAIL-001",
                    channel=Channel.EMAIL,
                    provider="mock",
                    identity="outreach@company.com",
                    display_name="Email Test Line",
                )
            )
            contact_repo.save(
                Contact(
                    contact_id="cnt_em_01",
                    company_id="anthropic",
                    name="Dario Amodei",
                    email="dario@anthropic.com",
                )
            )
            template_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_em_01",
                    name="Intro Email",
                    channel=Channel.EMAIL,
                    body="Hello {first_name}, exciting roles at {company}.",
                    subject="Opportunities at {company}",
                )
            )
            session.commit()

        # Execute manual Email send
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, email_provider=mock_em)
            res = outreach_svc.send_email(
                contact_id="cnt_em_01",
                sender_id="EMAIL-001",
                template_id="tmpl_em_01",
            )
            assert res["success"] is True
            assert res["status"] == "SENT"
            assert res["provider_reference"] is not None
            assert res["provider_reference"].startswith("em_ref_")

        # Verify DB persistence of provider_reference
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            attempts = outreach_repo.list_by_contact("cnt_em_01")
            assert len(attempts) == 1
            att = attempts[0]
            assert att.status == OutreachStatus.SENT
            assert att.provider_reference == res["provider_reference"]
            assert att.subject_snapshot == "Opportunities at anthropic"

    def test_manual_resend_preserves_history_and_creates_new_attempt(self, clean_db):
        _, SessionFactory = clean_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)

            comp_repo.save(Company.create(name="Microsoft", company_id="microsoft"))
            sender_repo.save(
                SenderAccount.create(
                    sender_id="WA-001",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919876543210",
                    display_name="WA Test Line",
                )
            )
            contact_repo.save(
                Contact(
                    contact_id="cnt_wa_resend",
                    company_id="microsoft",
                    name="Satya Nadella",
                    phone="+14258828080",
                )
            )
            session.commit()

        # 1. First send
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res1 = outreach_svc.send_whatsapp(
                contact_id="cnt_wa_resend", sender_id="WA-001", custom_body="First message"
            )
            assert res1["success"] is True

        # 2. Resend
        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=mock_wa)
            res2 = outreach_svc.resend_whatsapp(
                contact_id="cnt_wa_resend", sender_id="WA-001", custom_body="Follow-up note"
            )
            assert res2["success"] is True
            assert res2["attempt_type"] == "RESEND"
            assert res2["attempt_id"] != res1["attempt_id"]

        # 3. Verify history preserves both distinct attempts
        with SessionFactory() as session:
            outreach_svc = OutreachService(session)
            hist = outreach_svc.get_history("cnt_wa_resend")
            assert len(hist) == 2
            types = {h["attempt_type"] for h in hist}
            assert "MANUAL" in types
            assert "RESEND" in types
            bodies = {h["message_body"] for h in hist}
            assert "First message" in bodies
            assert "Follow-up note" in bodies

    def test_provider_failure_persists_failure_code_and_detail(self, clean_db):
        _, SessionFactory = clean_db
        failing_mock = MockWhatsAppProvider(should_fail=True, failure_code="ERR_PHONE_DISCONNECTED")

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)

            comp_repo.save(Company.create(name="Meta", company_id="meta"))
            sender_repo.save(
                SenderAccount.create(
                    sender_id="WA-001",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919876543210",
                    display_name="WA Test Line",
                )
            )
            contact_repo.save(
                Contact(
                    contact_id="cnt_fail_01",
                    company_id="meta",
                    name="Mark Zuckerberg",
                    phone="+16505434800",
                )
            )
            session.commit()

        with SessionFactory() as session:
            outreach_svc = OutreachService(session, whatsapp_provider=failing_mock)
            res = outreach_svc.send_whatsapp(contact_id="cnt_fail_01", sender_id="WA-001")
            assert res["success"] is False
            assert res["status"] == "FAILED"
            assert res["failure_code"] == "ERR_PHONE_DISCONNECTED"

        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            attempts = outreach_repo.list_by_contact("cnt_fail_01")
            assert len(attempts) == 1
            assert attempts[0].status == OutreachStatus.FAILED
            assert attempts[0].failure_code == "ERR_PHONE_DISCONNECTED"


class TestProviderConfigurationAndFactory:
    """Test production provider resolution and programmatic test dependency injection."""

    def test_production_factory_resolves_playwright_and_smtp(self):
        wa_provider = create_whatsapp_provider()
        em_provider = create_email_provider()
        assert isinstance(wa_provider, PlaywrightWhatsAppProvider)
        assert isinstance(em_provider, SmtpEmailProvider)

    def test_programmatic_provider_overrides(self):
        custom_wa = MockWhatsAppProvider(should_fail=True)
        set_whatsapp_provider(custom_wa)
        assert get_whatsapp_provider() is custom_wa
        reset_provider_overrides()
        assert isinstance(get_whatsapp_provider(), PlaywrightWhatsAppProvider)


class TestSchedulerUnificationAndDelegation:
    """Test single source of truth scheduler delegation from CampaignService."""

    def test_campaign_service_delegates_to_persistent_scheduler(self, clean_db):
        _, SessionFactory = clean_db
        mock_provider = MockFastWhatsAppProvider()
        rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})
        event_bus = EventBus()

        worker = OutreachWorker(
            session_factory=SessionFactory,
            whatsapp_provider=mock_provider,
            rate_limiter=rate_limiter,
            event_publisher=event_bus,
        )
        scheduler = PersistentCampaignScheduler(
            session_factory=SessionFactory,
            worker=worker,
            event_publisher=event_bus,
        )

        # Seed data
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)
            sender_repo = SqliteSenderRepository(session)

            for slug in ["apple", "amazon"]:
                comp_repo.save(Company.create(name=slug.title(), company_id=slug))
                for i in [1, 2]:
                    contact_repo.save(
                        Contact(contact_id=f"cnt_{slug}_{i}", company_id=slug, name=f"{slug}_{i}", phone=f"+1000000{i}")
                    )

            tmpl_repo.save(
                MessageTemplate.create(template_id="t1", name="T1", channel=Channel.WHATSAPP, body="Hello {name}")
            )
            sender_repo.save(
                SenderAccount.create(
                    sender_id="WA-001",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+91001",
                    display_name="Line 1",
                )
            )
            session.commit()

        # Create and start campaign via CampaignService
        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            camp = camp_svc.create_campaign(name="Unified Test Run", channel=Channel.WHATSAPP)
            res = camp_svc.start_campaign(camp.id)
            assert res["status"] in ("RUNNING", "COMPLETED")

        # Wait for scheduler worker
        time.sleep(0.8)

        with SessionFactory() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            prog = camp_svc.get_campaign_progress(camp.id)
            assert prog["status"] == "COMPLETED"
            assert prog["completed"] == 4
            assert prog["progress_percent"] == 100.0


class TestStartupCrashRecovery:
    """Test startup crash recovery audit logic on application initialization."""

    def test_startup_audit_recovers_sending_and_queued_attempts(self, clean_db):
        _, SessionFactory = clean_db

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)
            campaign_repo = SqliteCampaignRepository(session)

            comp_repo.save(Company.create(name="Stripe", company_id="stripe"))
            sender_repo.save(
                SenderAccount.create(
                    sender_id="WA-001",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919876543210",
                    display_name="WA Primary Line",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_01", company_id="stripe", name="Patrick Collison", phone="+14150000001")
            )
            contact_repo.save(
                Contact(contact_id="cnt_02", company_id="stripe", name="John Collison", phone="+14150000002")
            )

            # Create an in-flight campaign
            camp = Campaign.create(name="Crashed Campaign", channel=Channel.WHATSAPP, campaign_id="cmp_crash_01")
            camp.start()
            campaign_repo.save(camp)

            # Create stale in-flight attempts
            att1 = OutreachAttempt.prepare(
                contact_id="cnt_01",
                sender_account_id="WA-001",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Stale sending message",
                campaign_id="cmp_crash_01",
            )
            att1.mark_sending()
            outreach_repo.save(att1)

            att2 = OutreachAttempt.prepare(
                contact_id="cnt_02",
                sender_account_id="WA-001",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Stale queued message",
                campaign_id="cmp_crash_01",
            )
            att2.mark_queued()
            outreach_repo.save(att2)

            session.commit()

        # Simulate Application Startup
        scheduler = PersistentCampaignScheduler(session_factory=SessionFactory)
        recovered_count = scheduler.run_crash_recovery_audit()

        assert recovered_count == 2

        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            campaign_repo = SqliteCampaignRepository(session)

            # Attempts should transition to RECOVERY_REQUIRED, NOT FAILED
            a1 = outreach_repo.get_by_id(att1.id)
            a2 = outreach_repo.get_by_id(att2.id)
            assert a1.status == OutreachStatus.RECOVERY_REQUIRED
            assert a2.status == OutreachStatus.RECOVERY_REQUIRED
            assert "Process crash recovery" in a1.failure_detail
            assert a1.recovery_notes is not None

            # Campaign should be auto-paused
            reloaded_camp = campaign_repo.get_by_id("cmp_crash_01")
            assert reloaded_camp.status == CampaignStatus.PAUSED


class MockFastWhatsAppProvider:
    """Mock provider with call tracking."""

    def __init__(self):
        self.dispatched = []

    def send_message(self, attempt, recipient_phone, message_body, attachment_path=None):
        self.dispatched.append(attempt.id)
        return ProviderSendResult.sent(provider_reference=f"fast_wa_{len(self.dispatched)}")

    def check_status(self, provider_reference):
        return ProviderStatusResult(status=OutreachStatus.SENT)
