"""Integration tests for Pre-Send Transactions, crash recovery audit, and unknown state handling."""

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
from app.infrastructure.repositories import (
    SqliteCampaignRepository,
    SqliteCompanyRepository,
    SqliteContactRepository,
    SqliteOutreachRepository,
    SqliteSenderRepository,
    SqliteTemplateRepository,
)
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.providers import ProviderSendResult


class MockFailingOrAmbiguousProvider:
    def __init__(self, mode="crash"):
        self.mode = mode

    def send_message(self, attempt, recipient_phone, message_body, attachment_path=None):
        if self.mode == "unknown":
            return ProviderSendResult.unknown("Network socket drop during transmission")
        elif self.mode == "recovery_required":
            return ProviderSendResult.recovery_required("Browser process killed unexpectedly")
        elif self.mode == "failed":
            return ProviderSendResult.failed("ERR_PHONE_INVALID", "WhatsApp rejected number")
        else:
            raise RuntimeError("Process crashed mid-send!")

    def check_status(self, provider_reference):
        from app.ports.providers import ProviderStatusResult

        return ProviderStatusResult(status=OutreachStatus.UNKNOWN)


@pytest.fixture
def recovery_env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery_test.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        comp_repo = SqliteCompanyRepository(session)
        contact_repo = SqliteContactRepository(session)
        tmpl_repo = SqliteTemplateRepository(session)
        sender_repo = SqliteSenderRepository(session)

        comp_repo.save(Company.create(name="Oracle", company_id="oracle"))
        contact_repo.save(
            Contact(
                contact_id="cnt_oracle_01",
                company_id="oracle",
                name="Larry Ellison",
                phone="919999999000",
            )
        )
        tmpl_repo.save(
            MessageTemplate.create(
                template_id="tmpl_wa_1",
                name="Oracle Pitch",
                channel=Channel.WHATSAPP,
                body="Hello Larry",
            )
        )
        sender_repo.save(
            SenderAccount.create(
                sender_id="snd_wa_1",
                channel=Channel.WHATSAPP,
                provider="mock",
                identity="+919999999999",
                display_name="Line 1",
            )
        )
        session.commit()

    return {"session_factory": SessionFactory}


class TestCrashRecoveryAndPreSend:
    def test_pre_send_transaction_handles_exception_and_records_unknown(self, recovery_env):
        session_factory = recovery_env["session_factory"]
        mock_provider = MockFailingOrAmbiguousProvider(mode="crash")
        rate_limiter = RateLimiter()
        event_bus = EventBus()

        worker = OutreachWorker(
            session_factory=session_factory,
            whatsapp_provider=mock_provider,
            rate_limiter=rate_limiter,
            event_publisher=event_bus,
        )

        with session_factory() as session:
            sender = SqliteSenderRepository(session).get_by_id("snd_wa_1")
            tmpl = SqliteTemplateRepository(session).get_by_id("tmpl_wa_1")

        attempt = worker.execute_attempt(
            contact_id="cnt_oracle_01",
            sender_account=sender,
            template=tmpl,
            attempt_type=AttemptType.AUTOMATIC,
        )

        assert attempt.status == OutreachStatus.UNKNOWN
        assert "Process crashed" in attempt.failure_detail

        with session_factory() as session:
            db_attempt = SqliteOutreachRepository(session).get_by_id(attempt.id)
            assert db_attempt is not None
            assert db_attempt.status == OutreachStatus.UNKNOWN

    def test_crash_recovery_audit_detects_and_flags_orphaned_in_flight_attempts(self, recovery_env):
        session_factory = recovery_env["session_factory"]
        rate_limiter = RateLimiter()
        event_bus = EventBus()
        worker = OutreachWorker(
            session_factory=session_factory,
            whatsapp_provider=MockFailingOrAmbiguousProvider(),
            rate_limiter=rate_limiter,
            event_publisher=event_bus,
        )
        scheduler = PersistentCampaignScheduler(
            session_factory=session_factory,
            worker=worker,
            event_publisher=event_bus,
        )

        # Simulate 2 orphaned attempts stuck in SENDING and QUEUED from a crashed process
        with session_factory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            camp_repo = SqliteCampaignRepository(session)

            camp = Campaign.create(name="Crashed Campaign", channel=Channel.WHATSAPP, campaign_id="cmp_crashed")
            camp.start()
            camp_repo.save(camp)

            att1 = OutreachAttempt.prepare(
                contact_id="cnt_oracle_01",
                sender_account_id="snd_wa_1",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Msg 1",
                campaign_id="cmp_crashed",
                idempotency_key="idemp_crash_1",
            )
            att1.mark_sending()
            outreach_repo.save(att1)

            att2 = OutreachAttempt.prepare(
                contact_id="cnt_oracle_01",
                sender_account_id="snd_wa_1",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Msg 2",
                campaign_id="cmp_crashed",
                idempotency_key="idemp_crash_2",
            )
            att2.mark_queued()
            outreach_repo.save(att2)
            session.commit()

        # Run startup crash recovery audit
        recovered_count = scheduler.run_crash_recovery_audit()
        assert recovered_count == 2

        with session_factory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            camp_repo = SqliteCampaignRepository(session)

            a1 = outreach_repo.get_by_id(att1.id)
            a2 = outreach_repo.get_by_id(att2.id)
            assert a1.status == OutreachStatus.RECOVERY_REQUIRED
            assert a2.status == OutreachStatus.RECOVERY_REQUIRED
            assert "crash recovery" in a1.recovery_notes.lower()

            c = camp_repo.get_by_id("cmp_crashed")
            assert c.status == CampaignStatus.PAUSED

    def test_retry_after_failed_attempt_creates_fresh_attempt(self, recovery_env):
        """When an earlier attempt FAILED, executing another attempt for the contact
        must create a fresh PREPARED attempt with a new idempotency key rather than
        crashing with 'Cannot begin sending from status FAILED'."""
        session_factory = recovery_env["session_factory"]
        mock_provider = MockFailingOrAmbiguousProvider(mode="failed")
        rate_limiter = RateLimiter()
        event_bus = EventBus()

        worker = OutreachWorker(
            session_factory=session_factory,
            whatsapp_provider=mock_provider,
            rate_limiter=rate_limiter,
            event_publisher=event_bus,
        )

        with session_factory() as session:
            sender = SqliteSenderRepository(session).get_by_id("snd_wa_1")
            tmpl = SqliteTemplateRepository(session).get_by_id("tmpl_wa_1")

        # 1. First execution fails via provider
        attempt1 = worker.execute_attempt(
            contact_id="cnt_oracle_01",
            sender_account=sender,
            template=tmpl,
            attempt_type=AttemptType.AUTOMATIC,
        )
        assert attempt1.status == OutreachStatus.FAILED

        # 2. Second execution for the same contact/sender/template
        # Must NOT raise ValueError("Cannot begin sending from status 'FAILED'")
        mock_provider.mode = "failed"
        attempt2 = worker.execute_attempt(
            contact_id="cnt_oracle_01",
            sender_account=sender,
            template=tmpl,
            attempt_type=AttemptType.AUTOMATIC,
        )
        assert attempt2.status == OutreachStatus.FAILED
        assert attempt2.id != attempt1.id
        assert attempt2.idempotency_key != attempt1.idempotency_key
