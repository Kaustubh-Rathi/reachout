"""Integration tests for Persistent Campaign Scheduler lifecycle, prioritization, and execution."""

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.composition import build_repositories
from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import CampaignStatus, Channel, OutreachStatus
from app.domain.errors import ValidationError
from app.domain.message_template import MessageTemplate
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
from app.ports.infrastructure import SystemClock
from app.ports.providers import ProviderSendResult
from tests.doubles.fake_providers import FakeEmailProvider


class MockFastWhatsAppProvider:
    """Mock WhatsApp provider for fast deterministic testing."""

    def __init__(self):
        self.dispatched = []

    def send_message(self, attempt, recipient_phone, message_body, attachment_path=None):
        self.dispatched.append(
            {
                "attempt_id": attempt.id,
                "phone": recipient_phone,
                "body": message_body,
            }
        )
        return ProviderSendResult.sent(provider_reference=f"mock_ref_{len(self.dispatched)}")


@pytest.fixture
def scheduler_env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler_test.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)

    mock_provider = MockFastWhatsAppProvider()
    rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})
    event_bus = EventBus()

    worker = OutreachWorker(
        session_factory=SessionFactory,
        whatsapp_provider=mock_provider,
        email_provider=FakeEmailProvider(),
        rate_limiter=rate_limiter,
        event_publisher=event_bus,
        repository_factory=build_repositories,
        clock=SystemClock(),
    )

    scheduler = PersistentCampaignScheduler(
        session_factory=SessionFactory,
        worker=worker,
        event_publisher=event_bus,
        repository_factory=build_repositories,
        clock=SystemClock(),
    )

    # Seed test data: 3 companies, 2 contacts each (A1, A2, B1, B2, C1, C2)
    with SessionFactory() as session:
        comp_repo = SqliteCompanyRepository(session)
        contact_repo = SqliteContactRepository(session)
        tmpl_repo = SqliteTemplateRepository(session)
        sender_repo = SqliteSenderRepository(session)

        for c_slug in ["comp_a", "comp_b", "comp_c"]:
            comp_repo.save(Company.create(name=f"Company {c_slug[-1].upper()}", company_id=c_slug))
            for idx in [1, 2]:
                contact_repo.save(
                    Contact(
                        contact_id=f"cnt_{c_slug}_{idx}",
                        company_id=c_slug,
                        name=f"Person {c_slug[-1].upper()}{idx}",
                        phone=f"91900000{c_slug[-1]}{idx}",
                    )
                )

        # Seed 2 templates for rotation
        tmpl_repo.save(
            MessageTemplate.create(
                template_id="tmpl_wa_1",
                name="Variant 1",
                channel=Channel.WHATSAPP,
                body="Hello {name} at {company} (V1)",
            )
        )
        tmpl_repo.save(
            MessageTemplate.create(
                template_id="tmpl_wa_2",
                name="Variant 2",
                channel=Channel.WHATSAPP,
                body="Hey {name} at {company} (V2)",
            )
        )

        # Seed sender
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

    return {
        "engine": engine,
        "session_factory": SessionFactory,
        "scheduler": scheduler,
        "worker": worker,
        "provider": mock_provider,
        "event_bus": event_bus,
    }


class TestSchedulerLifecycle:
    def test_campaign_company_first_prioritization_and_completion(self, scheduler_env):
        session_factory = scheduler_env["session_factory"]
        scheduler = scheduler_env["scheduler"]

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp = Campaign.create(
                name="Fast Test Campaign",
                channel=Channel.WHATSAPP,
                template_ids=["tmpl_wa_1", "tmpl_wa_2"],
                sender_account_ids=["snd_wa_1"],
                campaign_id="cmp_test_01",
            )
            camp_repo.save(camp)
            session.commit()

        # Start campaign
        scheduler.start_campaign("cmp_test_01")

        # Wait for campaign to process all 6 contacts (poll rather than a fixed
        # sleep so the test is not timing-flaky on a loaded CI machine).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with session_factory() as probe:
                if SqliteCampaignRepository(probe).get_by_id("cmp_test_01").status == CampaignStatus.COMPLETED:
                    break
            time.sleep(0.05)

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            camp = camp_repo.get_by_id("cmp_test_01")
            assert camp.status == CampaignStatus.COMPLETED

            attempts = outreach_repo.list_by_campaign("cmp_test_01")
            assert len(attempts) == 6
            assert all(a.status == OutreachStatus.SENT for a in attempts)

            # Verify company-first ordering: comp_a, comp_b, comp_c, comp_a, comp_b, comp_c
            contact_ids = [a.contact_id for a in attempts]
            expected_prefix = ["cnt_comp_a", "cnt_comp_b", "cnt_comp_c", "cnt_comp_a", "cnt_comp_b", "cnt_comp_c"]
            for cid, exp_pref in zip(contact_ids, expected_prefix, strict=False):
                assert cid.startswith(exp_pref)

            # Verify template rotation: V1, V2, V1, V2...
            tmpl_ids = [a.template_id for a in attempts]
            assert tmpl_ids == ["tmpl_wa_1", "tmpl_wa_2", "tmpl_wa_1", "tmpl_wa_2", "tmpl_wa_1", "tmpl_wa_2"]

    def test_campaign_pause_and_resume(self, scheduler_env):
        session_factory = scheduler_env["session_factory"]
        scheduler = scheduler_env["scheduler"]

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp = Campaign.create(
                name="Pause Test Campaign",
                channel=Channel.WHATSAPP,
                template_ids=["tmpl_wa_1"],
                sender_account_ids=["snd_wa_1"],
                campaign_id="cmp_pause_01",
            )
            camp_repo.save(camp)
            session.commit()

        scheduler.start_campaign("cmp_pause_01")
        scheduler.pause_campaign("cmp_pause_01")

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp = camp_repo.get_by_id("cmp_pause_01")
            assert camp.status == CampaignStatus.PAUSED

        scheduler.resume_campaign("cmp_pause_01")
        time.sleep(1.0)

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp = camp_repo.get_by_id("cmp_pause_01")
            assert camp.status in (CampaignStatus.RUNNING, CampaignStatus.COMPLETED)

    def test_start_and_resume_refuse_unresolvable_template_attachment(self, scheduler_env, tmp_path):
        """A bad template attachment path fails fast once instead of failing every candidate."""
        session_factory = scheduler_env["session_factory"]
        scheduler = scheduler_env["scheduler"]
        missing = str(tmp_path / "no-such-resume.pdf")

        with session_factory() as session:
            SqliteTemplateRepository(session).save(
                MessageTemplate.create(
                    template_id="tmpl_wa_bad",
                    name="Bad attachment",
                    channel=Channel.WHATSAPP,
                    body="Hello {name}",
                    attachment_ref=f'"{missing}"',
                )
            )
            camp_repo = SqliteCampaignRepository(session)
            camp_repo.save(
                Campaign.create(
                    name="Bad Attachment Campaign",
                    channel=Channel.WHATSAPP,
                    template_ids=["tmpl_wa_bad"],
                    sender_account_ids=["snd_wa_1"],
                    campaign_id="cmp_bad_attach_01",
                )
            )
            session.commit()

        with pytest.raises(ValidationError, match="not found"):
            scheduler.start_campaign("cmp_bad_attach_01")

        with session_factory() as session:
            camp = SqliteCampaignRepository(session).get_by_id("cmp_bad_attach_01")
            assert camp.status == CampaignStatus.IDLE
        assert not scheduler.is_running("cmp_bad_attach_01")

        with session_factory() as session:
            camp_repo = SqliteCampaignRepository(session)
            camp_repo.save(
                Campaign.create(
                    name="Bad Attachment Resume Campaign",
                    channel=Channel.WHATSAPP,
                    template_ids=["tmpl_wa_bad"],
                    sender_account_ids=["snd_wa_1"],
                    campaign_id="cmp_bad_attach_02",
                )
            )
            camp_repo.set_status("cmp_bad_attach_02", CampaignStatus.PAUSED)
            session.commit()

        with pytest.raises(ValidationError, match="not found"):
            scheduler.resume_campaign("cmp_bad_attach_02")

        with session_factory() as session:
            camp = SqliteCampaignRepository(session).get_by_id("cmp_bad_attach_02")
            assert camp.status == CampaignStatus.PAUSED
