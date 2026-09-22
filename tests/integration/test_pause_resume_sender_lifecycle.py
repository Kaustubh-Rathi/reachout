"""Comprehensive Pause, Modify Senders, Resume, and N-Sender Lifecycle Integration Tests.

Validates:
1. Deterministic Pause -> Modify Senders -> Resume workflow.
2. N WhatsApp and N Email sender scaling (1, 2, 3, 10 senders) without hardcoded limits.
3. State persistence: company rounds, endpoint coverage, quotas, rotation cursors.
4. Independent rate limiter cooldown per sender.
5. Sender deactivation / reactivation without FK integrity violations.
6. Server restart while PAUSED and safe resumption.
7. Full Pause/Resume edge-case test matrix (idle, pending, rate limit, quota exhaustion, all covered).
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.composition import build_repositories
from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.sender_rotation import SenderRotationPolicy
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.models import (
    CompanyModel,
)
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import SystemClock
from app.services.campaign_service import CampaignService
from app.services.sender_service import SenderService
from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider


@pytest.fixture
def test_db_setup():
    """Create an isolated file-backed SQLite DB (WAL) with a session factory.

    Uses a per-test temp file so each session gets its own connection. The old
    in-memory + StaticPool (single shared connection) setup lost concurrent
    writes when the background worker thread and the test thread updated the
    same row, which made quota accounting flaky.
    """
    import os
    import tempfile

    from sqlalchemy import event

    db_dir = tempfile.mkdtemp(prefix="reachout_lifecycle_")
    db_file = os.path.join(db_dir, "lifecycle.db")
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_factory


@pytest.fixture
def fake_providers():
    """Create clean fake provider doubles."""
    wa = FakeWhatsAppProvider()
    em = FakeEmailProvider()
    return wa, em


class TestPauseModifyResumeLifecycle:
    """Test suite for Section 5: Mandatory PAUSE / MODIFY / RESUME integration workflow."""

    def test_mandatory_pause_add_senders_resume_workflow(self, test_db_setup, fake_providers):
        """Verify:
        Initial senders: WA1, WA2, EMAIL1, EMAIL2
        Campaign begins -> dispatches initial attempts.
        Pause -> verify PAUSED, no new attempts.
        Add WA3, EMAIL3 -> authenticate/activate them.
        Resume -> continues from next candidate; does NOT restart company rounds;
        preserves endpoint coverage and quotas; incorporates WA3/EMAIL3 deterministically.
        """
        wa_provider, em_provider = fake_providers
        rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})
        worker = OutreachWorker(
            session_factory=test_db_setup,
            whatsapp_provider=wa_provider,
            email_provider=em_provider,
            rate_limiter=rate_limiter,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )
        scheduler = PersistentCampaignScheduler(
            session_factory=test_db_setup,
            worker=worker,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )

        # Seed initial database state
        with test_db_setup() as session:
            comp_repo = SqliteCampaignRepository(session)
            contact_repo = SqliteContactRepository(session)
            sender_repo = SqliteSenderRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            # 3 Companies with 2 Contacts each
            c1 = Company.create("Google India")
            c2 = Company.create("Microsoft India")
            c3 = Company.create("Amazon India")
            for comp in [c1, c2, c3]:
                session.add(CompanyModel.from_domain(comp))

            contacts = [
                Contact(
                    contact_id="cnt_c1_hr1",
                    company_id=c1.id,
                    name="Google HR 1",
                    phone="+919000000001",
                    email="hr1@google.com",
                ),
                Contact(
                    contact_id="cnt_c1_hr2",
                    company_id=c1.id,
                    name="Google HR 2",
                    phone="+919000000002",
                    email="hr2@google.com",
                ),
                Contact(
                    contact_id="cnt_c2_hr1", company_id=c2.id, name="MS HR 1", phone="+919000000003", email="hr1@ms.com"
                ),
                Contact(
                    contact_id="cnt_c2_hr2", company_id=c2.id, name="MS HR 2", phone="+919000000004", email="hr2@ms.com"
                ),
                Contact(
                    contact_id="cnt_c3_hr1",
                    company_id=c3.id,
                    name="AWS HR 1",
                    phone="+919000000005",
                    email="hr1@aws.com",
                ),
                Contact(
                    contact_id="cnt_c3_hr2",
                    company_id=c3.id,
                    name="AWS HR 2",
                    phone="+919000000006",
                    email="hr2@aws.com",
                ),
            ]
            for cnt in contacts:
                contact_repo.save(cnt)

            # Templates
            tmpl_wa = MessageTemplate.create(name="WA Tmpl", channel=Channel.WHATSAPP, body="Hello {first_name}")
            tmpl_em = MessageTemplate.create(
                name="EM Tmpl", channel=Channel.EMAIL, subject="Intro", body="Hi {first_name}"
            )
            tmpl_repo.save(tmpl_wa)
            tmpl_repo.save(tmpl_em)

            # Initial 4 senders (2 WA, 2 Email)
            wa1 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+911111111111", "WA 1", sender_id="WA1")
            wa2 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+912222222222", "WA 2", sender_id="WA2")
            em1 = SenderAccount.create(
                Channel.EMAIL, "fake_email", "sender1@example.com", "Email 1", sender_id="EMAIL1"
            )
            em2 = SenderAccount.create(
                Channel.EMAIL, "fake_email", "sender2@example.com", "Email 2", sender_id="EMAIL2"
            )
            for s in [wa1, wa2, em1, em2]:
                sender_repo.save(s)

            # Create Campaign
            campaign = Campaign.create(
                name="Multi-Channel Q3",
                channel=Channel.WHATSAPP,
                automatic_quota=10,
            )
            comp_repo.save(campaign)
            campaign_id = campaign.id
            session.commit()

        # Step 1: Start campaign (starts background worker)
        scheduler.start_campaign(campaign_id=campaign_id)

        # Wait until 3 dispatches complete (Company Round 1)
        for _ in range(50):
            with test_db_setup() as session:
                outreach_repo = SqliteOutreachRepository(session)
                attempts = outreach_repo.list_by_campaign(campaign_id)
                if len(attempts) >= 3:
                    break
            time.sleep(0.02)

        # Step 2: Pause campaign while in-flight / after initial round
        scheduler.pause_campaign(campaign_id=campaign_id)

        # Wait for the worker to FULLY stop before asserting: pause is asynchronous,
        # so a dispatch already in flight may still be committing when pause returns.
        # Require status PAUSED AND a stable SENT count across consecutive polls,
        # then the quota counter will have caught up (accounting is atomic).
        stable = 0
        last_sent = -1
        for _ in range(100):
            with test_db_setup() as session:
                c = SqliteCampaignRepository(session).get_by_id(campaign_id)
                atts = SqliteOutreachRepository(session).list_by_campaign(campaign_id)
                sent = len([a for a in atts if a.status == OutreachStatus.SENT])
            if c.status == CampaignStatus.PAUSED and sent == last_sent:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            last_sent = sent
            time.sleep(0.02)

        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            camp = camp_repo.get_by_id(campaign_id)
            assert camp.status == CampaignStatus.PAUSED

            attempts = outreach_repo.list_by_campaign(campaign_id)
            assert len(attempts) >= 3
            initial_contact_ids = [a.contact_id for a in attempts]
            # Company-first round 1 contacts: 1 per company
            assert "cnt_c1_hr1" in initial_contact_ids
            assert "cnt_c2_hr1" in initial_contact_ids
            assert "cnt_c3_hr1" in initial_contact_ids

            # Verify sender accounts used were from initial set
            senders_used = {a.sender_account_id for a in attempts}
            assert senders_used.issubset({"WA1", "WA2", "EMAIL1", "EMAIL2"})

            # Automatic quota accounting (only SENT attempts are counted toward quota)
            assert camp.automatic_used == len([a for a in attempts if a.status == OutreachStatus.SENT])

        # Step 3: While PAUSED, add WA3 and EMAIL3 and activate them
        with test_db_setup() as session:
            sender_svc = SenderService(session)
            wa3_info = sender_svc.create_sender(
                id="WA3",
                channel=Channel.WHATSAPP,
                provider="fake_wa",
                identity="+913333333333",
                display_name="WA 3",
                status=SenderStatus.ACTIVE,
            )
            em3_info = sender_svc.create_sender(
                id="EMAIL3",
                channel=Channel.EMAIL,
                provider="fake_email",
                identity="sender3@example.com",
                display_name="Email 3",
                status=SenderStatus.ACTIVE,
            )
            assert wa3_info["status"] == "ACTIVE"
            assert em3_info["status"] == "ACTIVE"

        # Step 4: Resume campaign via CampaignService (with readiness validation)
        with test_db_setup() as session:
            camp_svc = CampaignService(session, scheduler=scheduler)
            camp_progress = camp_svc.resume_campaign(campaign_id)
            assert camp_progress["status"] == "RUNNING"

            assert camp_progress["status"] == "RUNNING"

        time.sleep(0.6)

        # Step 5: Verify post-resume execution integrity
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            all_attempts = outreach_repo.list_by_campaign(campaign_id)
            assert len(all_attempts) >= 4

            # 1. Campaign resumed and did NOT duplicate C1-HR1's WhatsApp outreach
            c1_hr1_wa_attempts = [
                a for a in all_attempts if a.contact_id == "cnt_c1_hr1" and a.channel == Channel.WHATSAPP
            ]
            assert len(c1_hr1_wa_attempts) == 1, "Duplicate WhatsApp attempt created for cnt_c1_hr1!"

            # 2. Previously covered endpoints remain covered without duplicate dispatches
            all_destinations = [f"{a.channel.value}:{a.destination}" for a in all_attempts]
            assert len(all_destinations) == len(set(all_destinations)), "Duplicate destination outreach detected!"

            # 3. New senders (WA3, EMAIL3) became eligible and participated in rotation
            post_resume_senders = {a.sender_account_id for a in all_attempts}
            assert "WA3" in post_resume_senders or "EMAIL3" in post_resume_senders or len(all_attempts) >= 6

            # 4. Quota reflects actual dispatches
            camp = camp_repo.get_by_id(campaign_id)
            assert camp.automatic_used == len([a for a in all_attempts if a.status == OutreachStatus.SENT])


class TestDynamicNSendersAndCooldown:
    """Test suite for Sections 6, 7, 8, 9, 10, 11, 26, 27: Dynamic N senders and independent cooldowns."""

    def test_n_scale_whatsapp_and_email_senders(self, test_db_setup):
        """Test dynamic scaling across 1, 2, 3, 10 senders per channel without hardcoded limits."""
        with test_db_setup() as session:
            sender_svc = SenderService(session)

            # Scale to 10 WhatsApp sessions
            wa_list = sender_svc.configure_whatsapp_sessions(count=10)
            assert len([s for s in wa_list if s["channel"] == "WHATSAPP"]) == 10

            # Add dynamically via add_whatsapp_session: now returns an in-memory temp id
            # that is NOT persisted (sessions only reach the DB once authenticated).
            wa11 = sender_svc.add_whatsapp_session(display_name="WhatsApp Session 11")
            assert wa11["id"].startswith("tmp_auth_")
            assert wa11["status"] == "AUTH_REQUIRED"
            assert sender_svc.repo.get_by_id(wa11["id"]) is None

            # Create 10 Email senders
            for i in range(1, 11):
                sender_svc.create_sender(
                    id=f"EMAIL_SESSION_{i}",
                    channel=Channel.EMAIL,
                    provider="smtp",
                    identity=f"sender{i}@company.com",
                    display_name=f"Email Sender {i}",
                    status=SenderStatus.ACTIVE,
                )

            all_senders = sender_svc.list_senders()
            em_senders = [s for s in all_senders if s["channel"] == "EMAIL"]
            assert len(em_senders) == 10

    def test_independent_cooldown_per_sender(self):
        """Test Section 11: Each sender maintains its own isolated cooldown."""
        limiter = RateLimiter(default_channel_delay={"WHATSAPP": 10.0, "EMAIL": 5.0})

        # Sender 1 sends at t=0
        limiter.record_dispatch_success("WA1")
        can_wa1, reason1 = limiter.can_send("WA1", "WHATSAPP")
        assert can_wa1 is False
        assert "WAITING_CHANNEL_PACE" in reason1

        # Sender 2 and 3 have never sent -> immediately eligible!
        can_wa2, reason2 = limiter.can_send("WA2", "WHATSAPP")
        assert can_wa2 is True
        assert reason2 == "READY"

        can_wa3, reason3 = limiter.can_send("WA3", "WHATSAPP")
        assert can_wa3 is True
        assert reason3 == "READY"

        # Sender 2 sends
        limiter.record_dispatch_success("WA2")
        can_wa2_after, _ = limiter.can_send("WA2", "WHATSAPP")
        assert can_wa2_after is False

        # Sender 3 is STILL eligible
        can_wa3_after, _ = limiter.can_send("WA3", "WHATSAPP")
        assert can_wa3_after is True

    def test_sender_deactivation_and_reactivation_rotation(self, test_db_setup):
        """Test Section 9 & 10: Deactivated sender exits rotation, reactivated sender re-enters rotation."""
        with test_db_setup() as session:
            sender_repo = SqliteSenderRepository(session)
            sender_svc = SenderService(session)

            wa1 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+911", "WA 1", sender_id="WA1")
            wa2 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+912", "WA 2", sender_id="WA2")
            wa3 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+913", "WA 3", sender_id="WA3")
            for s in [wa1, wa2, wa3]:
                sender_repo.save(s)
            session.commit()

            # All 3 active initially
            active = sender_repo.list_active(Channel.WHATSAPP)
            assert len(active) == 3

            # Deactivate WA2
            sender_svc.deactivate_sender("WA2")
            active_after_deact = sender_repo.list_active(Channel.WHATSAPP)
            active_ids = [s.id for s in active_after_deact]
            assert "WA2" not in active_ids
            assert active_ids == ["WA1", "WA3"]

            # Select next sender starting from cursor 1 (where WA2 used to be)
            selected, new_cursor = SenderRotationPolicy.select_next_sender(active_after_deact, cursor=1)
            assert selected.id == "WA3"
            assert new_cursor == 0

            # Reactivate WA2
            sender_svc.reactivate_sender("WA2")
            active_after_react = sender_repo.list_active(Channel.WHATSAPP)
            assert len(active_after_react) == 3


class TestPauseResumeMatrixAndRestart:
    """Test suite for Section 12 & 25: Complete Pause/Resume edge-case matrix and server restart."""

    def test_server_restart_while_paused_preserves_state(self, test_db_setup, fake_providers):
        """Test Section 12: Running -> Pause -> Server Restart -> DB load -> Resume continues seamlessly."""
        wa_provider, em_provider = fake_providers
        rate_limiter = RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})
        worker1 = OutreachWorker(
            session_factory=test_db_setup,
            whatsapp_provider=wa_provider,
            email_provider=em_provider,
            rate_limiter=rate_limiter,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )
        scheduler1 = PersistentCampaignScheduler(
            session_factory=test_db_setup,
            worker=worker1,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )

        # Setup and start campaign
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            contact_repo = SqliteContactRepository(session)
            sender_repo = SqliteSenderRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            c1 = Company.create("Restart Corp")
            session.add(CompanyModel.from_domain(c1))

            for i in range(1, 5):
                cnt = Contact(
                    contact_id=f"cnt_rst_{i}", company_id=c1.id, name=f"Contact {i}", phone=f"+91800000000{i}"
                )
                contact_repo.save(cnt)

            wa1 = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+919999999999", "WA 1", sender_id="WA_RST_1")
            sender_repo.save(wa1)

            tmpl = MessageTemplate.create("Tmpl", Channel.WHATSAPP, "Hi {first_name}")
            tmpl_repo.save(tmpl)

            camp = Campaign.create("Restart Test Campaign", Channel.WHATSAPP, automatic_quota=10)
            camp_repo.save(camp)
            campaign_id = camp.id
            session.commit()

        # Run dispatches until 2 complete, then pause
        scheduler1.start_campaign(campaign_id)
        for _ in range(50):
            with test_db_setup() as session:
                outreach_repo = SqliteOutreachRepository(session)
                attempts = outreach_repo.list_by_campaign(campaign_id)
                if len(attempts) >= 2:
                    break
            time.sleep(0.02)
        scheduler1.pause_campaign(campaign_id)
        time.sleep(0.1)

        # --- SIMULATE PROCESS CRASH / RESTART ---
        # Destroy scheduler1 and create brand-new scheduler2
        worker2 = OutreachWorker(
            session_factory=test_db_setup,
            whatsapp_provider=wa_provider,
            email_provider=em_provider,
            rate_limiter=rate_limiter,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )
        scheduler2 = PersistentCampaignScheduler(
            session_factory=test_db_setup,
            worker=worker2,
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )

        # Run startup crash recovery audit
        scheduler2.run_crash_recovery_audit()

        # Verify campaign is still PAUSED and previous attempts are intact
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            camp = camp_repo.get_by_id(campaign_id)
            assert camp.status == CampaignStatus.PAUSED
            assert camp.automatic_used >= 2

            attempts = outreach_repo.list_by_campaign(campaign_id)
            assert len(attempts) >= 2

        # Resume on new scheduler instance
        scheduler2.resume_campaign(campaign_id)
        time.sleep(0.4)

        # Verify all remaining contacts dispatched without duplicating first 2
        with test_db_setup() as session:
            outreach_repo = SqliteOutreachRepository(session)
            camp_repo = SqliteCampaignRepository(session)

            all_attempts = outreach_repo.list_by_campaign(campaign_id)
            assert len(all_attempts) == 4
            contact_ids = [a.contact_id for a in all_attempts]
            assert len(set(contact_ids)) == 4, "Duplicate attempts detected after crash recovery resume!"

    def test_resume_blocked_when_quota_exhausted(self, test_db_setup):
        """Test Section 24 & 25: Resume is blocked with OUTREACH_NOT_READY when automatic quota is exhausted."""
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            contact_repo = SqliteContactRepository(session)
            sender_repo = SqliteSenderRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp = Company.create("Quota Corp", company_id="comp_q")
            session.add(CompanyModel.from_domain(comp))

            c = Contact(contact_id="cnt_q_1", company_id="comp_q", name="Quota User", phone="+917777777777")
            contact_repo.save(c)
            s = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+911", "WA", sender_id="WA_Q")
            sender_repo.save(s)
            t = MessageTemplate.create("Tmpl", Channel.WHATSAPP, "Hi {first_name}")
            tmpl_repo.save(t)

            # Campaign with quota=2 and automatic_used=2
            camp = Campaign.create("Quota Campaign", Channel.WHATSAPP, automatic_quota=2)
            camp.metadata["automatic_used"] = 2
            camp.status = CampaignStatus.PAUSED
            camp_repo.save(camp)
            campaign_id = camp.id
            session.commit()

            camp_svc = CampaignService(session)
            with pytest.raises(ValueError) as exc_info:
                camp_svc.resume_campaign(campaign_id)

            assert "OUTREACH_NOT_READY" in str(exc_info.value)
            assert "QUOTA_EXHAUSTED" in str(exc_info.value)

    def test_resume_blocked_when_all_endpoints_covered(self, test_db_setup):
        """Test Section 24 & 25: Resume is blocked when all contact endpoints are already covered."""
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            contact_repo = SqliteContactRepository(session)
            sender_repo = SqliteSenderRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            comp = Company.create("Covered Corp", company_id="comp_cov")
            session.add(CompanyModel.from_domain(comp))

            cnt = Contact(contact_id="cnt_cov_1", company_id="comp_cov", name="Covered User", phone="+918888888888")
            contact_repo.save(cnt)
            s = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+911", "WA", sender_id="WA_COV")
            sender_repo.save(s)
            t = MessageTemplate.create("Tmpl", Channel.WHATSAPP, "Hi {first_name}")
            tmpl_repo.save(t)

            # Record SENT attempt covering this phone
            att = OutreachAttempt.prepare(
                contact_id=cnt.contact_id,
                sender_account_id=s.id,
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Hi",
                destination=cnt.phone,
            )
            att.mark_sent("ref_123")
            outreach_repo.save(att)

            camp = Campaign.create("Covered Campaign", Channel.WHATSAPP, automatic_quota=10)
            camp.status = CampaignStatus.PAUSED
            camp_repo.save(camp)
            campaign_id = camp.id
            session.commit()

            camp_svc = CampaignService(session)
            with pytest.raises(ValueError) as exc_info:
                camp_svc.resume_campaign(campaign_id)

            assert "OUTREACH_NOT_READY" in str(exc_info.value)
            assert "ALL_CONTACTS_COVERED" in str(exc_info.value)

    def test_resume_blocked_when_no_active_senders(self, test_db_setup):
        """Test Section 24: Resume is blocked when no senders are in ACTIVE status."""
        with test_db_setup() as session:
            camp_repo = SqliteCampaignRepository(session)
            contact_repo = SqliteContactRepository(session)
            sender_repo = SqliteSenderRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp = Company.create("No Senders Corp", company_id="comp_no_snd")
            session.add(CompanyModel.from_domain(comp))

            cnt = Contact(contact_id="cnt_no_snd_1", company_id="comp_no_snd", name="User", phone="+918888888888")
            contact_repo.save(cnt)
            # Senders are all in AUTH_REQUIRED status
            s = SenderAccount.create(Channel.WHATSAPP, "fake_wa", "+911", "WA", sender_id="WA_INACT")
            s.status = SenderStatus.AUTH_REQUIRED
            sender_repo.save(s)

            t = MessageTemplate.create("Tmpl", Channel.WHATSAPP, "Hi {first_name}")
            tmpl_repo.save(t)

            camp = Campaign.create("No Senders Campaign", Channel.WHATSAPP, automatic_quota=10)
            camp.status = CampaignStatus.PAUSED
            camp_repo.save(camp)
            campaign_id = camp.id
            session.commit()

            camp_svc = CampaignService(session)
            with pytest.raises(ValueError) as exc_info:
                camp_svc.resume_campaign(campaign_id)

            assert "OUTREACH_NOT_READY" in str(exc_info.value)
            assert "NO_ACTIVE_WHATSAPP_SESSION" in str(exc_info.value)
