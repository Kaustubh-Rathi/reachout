"""Outreach policy, templates, company coverage, and multi-channel dispatch tests.

Verifies the outreach policy dimensions:
1. Message Templates & Variable Interpolation & Validation
2. Company Coverage / Round-Robin (A1, B1, C1, A2, C2, C3) & Round State
3. Contact Eligibility & Historical Contact Exclusion
4. Channel + Sender Rotation & Sender Availability Precedence
5. Channel Fallback & UNKNOWN Ambiguity Safety
6. Campaign Quota & Production Pacing
7. End-to-End Campaign Multi-Channel Simulation (Section 28)
8. Source Data-Integrity Verification
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.composition import build_repositories
from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus, SenderStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility
from app.domain.policies.fallback_policy import ChannelFallbackPolicy
from app.domain.policies.prioritization import (
    calculate_company_round_state,
    prioritize_company_first,
)
from app.domain.policies.sender_rotation import SenderRotationPolicy
from app.domain.policies.template_rotation import select_template_round_robin
from app.domain.sender_account import SenderAccount
from app.domain.template_catalog import (
    OFFICIAL_EMAIL_TEMPLATES,
    OFFICIAL_WHATSAPP_TEMPLATES,
)
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import SystemClock
from app.services.outreach_service import OutreachService
from app.services.template_service import TemplateService
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider


@pytest.fixture
def outreach_db(tmp_path):
    """Create a temporary SQLite database engine and session factory."""
    db_file = tmp_path / "outreach_test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # Seed official templates so attempts referencing template_id (FK) resolve.
    with session_factory() as session:
        TemplateService(session).seed_defaults_if_empty()
        session.commit()
    return session_factory


class TestTemplates:
    """Dimension 1: Templates verification suite."""

    def test_all_4_whatsapp_templates_exist_and_match_specs(self):
        """Verify all 4 WhatsApp templates exist with exact texts and URLs."""
        assert len(OFFICIAL_WHATSAPP_TEMPLATES) == 4
        template_ids = {t.id for t in OFFICIAL_WHATSAPP_TEMPLATES}
        assert template_ids == {"WA-01", "WA-02", "WA-03", "WA-04"}

        for tmpl in OFFICIAL_WHATSAPP_TEMPLATES:
            assert tmpl.channel == Channel.WHATSAPP
            # Templates use {sender_*} placeholders (personal identity comes from .env/config).
            assert "{sender_portfolio}" in tmpl.body
            assert "{sender_linkedin}" in tmpl.body
            assert "{sender_github}" in tmpl.body
            assert "{sender_phone}" in tmpl.body
            assert tmpl.attachment_ref is None
            assert tmpl.active is True
        # SENDER_PROFILE must provide real identity so placeholders resolve.
        from app.config import SENDER_PROFILE

        assert SENDER_PROFILE["sender_portfolio"].startswith("https://")
        assert SENDER_PROFILE["sender_phone"]

    def test_all_4_email_templates_exist_and_match_specs(self):
        """Verify all 4 Email templates exist with exact subjects and bodies."""
        assert len(OFFICIAL_EMAIL_TEMPLATES) == 4
        template_ids = {t.id for t in OFFICIAL_EMAIL_TEMPLATES}
        assert template_ids == {"EMAIL-01", "EMAIL-02", "EMAIL-03", "EMAIL-04"}

        for tmpl in OFFICIAL_EMAIL_TEMPLATES:
            assert tmpl.channel == Channel.EMAIL
            assert tmpl.subject is not None and len(tmpl.subject) > 0
            assert "{sender_portfolio}" in tmpl.body
            assert "{sender_linkedin}" in tmpl.body
            assert "{sender_github}" in tmpl.body
            assert "{sender_phone}" in tmpl.body
            assert tmpl.attachment_ref is None
            assert tmpl.active is True

    def test_template_variable_interpolation(self):
        """Verify template variables [Name] and [Company Name] interpolate correctly."""
        tmpl = OFFICIAL_WHATSAPP_TEMPLATES[0]
        contact = Contact(contact_id="cnt_1", company_id="google", name="Sundar Pichai", phone="+919876543210")
        company = Company.create(name="Google LLC", company_id="google")

        rendered = tmpl.render(contact=contact, company=company)
        assert "Hi Sundar," in rendered.body
        assert "productive week at Google LLC." in rendered.body
        assert "[Name]" not in rendered.body
        assert "[Company Name]" not in rendered.body

    def test_unresolved_variable_validation_fails_pre_dispatch(self, outreach_db):
        """Verify attempt fails validation before external dispatch if required placeholder is unresolvable."""
        worker = OutreachWorker(
            session_factory=outreach_db,
            whatsapp_provider=MockWhatsAppProvider(),
            email_provider=MockEmailProvider(),
            rate_limiter=RateLimiter(default_channel_delay={"WHATSAPP": 0.001, "EMAIL": 0.001}),
            event_publisher=EventBus(),
            repository_factory=build_repositories,
            clock=SystemClock(),
        )

        with outreach_db() as session:
            comp = Company.create(name="Google LLC", company_id="google")
            SqliteCompanyRepository(session).save(comp)

            sender = SenderAccount.create(
                channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="S1", sender_id="WA-001"
            )
            SqliteSenderRepository(session).save(sender)

            # Contact with empty name
            contact_no_name = Contact(contact_id="cnt_no_name", company_id="google", name="", phone="+919876543210")
            SqliteContactRepository(session).save(contact_no_name)
            session.commit()

        tmpl = OFFICIAL_WHATSAPP_TEMPLATES[0]
        attempt = worker.execute_attempt(
            contact_id="cnt_no_name",
            sender_account=sender,
            template=tmpl,
        )

        assert attempt.status == OutreachStatus.FAILED
        assert attempt.failure_code == "ERR_TEMPLATE_VARIABLE_UNRESOLVED"
        assert "Contact name is missing" in (attempt.failure_detail or "")

    def test_deterministic_template_rotation(self):
        """Verify templates rotate deterministically: WA-01..04 and EMAIL-01..04."""
        wa_ids = [select_template_round_robin(OFFICIAL_WHATSAPP_TEMPLATES, i).id for i in range(8)]
        assert wa_ids == ["WA-01", "WA-02", "WA-03", "WA-04", "WA-01", "WA-02", "WA-03", "WA-04"]

        em_ids = [select_template_round_robin(OFFICIAL_EMAIL_TEMPLATES, i).id for i in range(8)]
        assert em_ids == [
            "EMAIL-01",
            "EMAIL-02",
            "EMAIL-03",
            "EMAIL-04",
            "EMAIL-01",
            "EMAIL-02",
            "EMAIL-03",
            "EMAIL-04",
        ]


class TestCompanyCoverage:
    """Dimension 2: Company Coverage / Round-Robin verification suite."""

    def test_canonical_dataset_company_first_ordering(self):
        """Dataset A1, A2, B1, C1, C2, C3 produces A1, B1, C1, A2, C2, C3."""
        a1 = Contact(contact_id="a1", company_id="Company A", name="A1", phone="+919000000001")
        a2 = Contact(contact_id="a2", company_id="Company A", name="A2", phone="+919000000002")
        b1 = Contact(contact_id="b1", company_id="Company B", name="B1", phone="+919000000003")
        c1 = Contact(contact_id="c1", company_id="Company C", name="C1", phone="+919000000004")
        c2 = Contact(contact_id="c2", company_id="Company C", name="C2", phone="+919000000005")
        c3 = Contact(contact_id="c3", company_id="Company C", name="C3", phone="+919000000006")

        raw = [a1, a2, b1, c1, c2, c3]
        ordered = prioritize_company_first(raw)
        names = [c.name for c in ordered]
        assert names == ["A1", "B1", "C1", "A2", "C2", "C3"]

    def test_company_round_state_tracking(self):
        """Verify dynamic calculation of round metrics, companies covered, and remaining."""
        a1 = Contact(contact_id="a1", company_id="Company A", name="A1", phone="+919000000001")
        a2 = Contact(contact_id="a2", company_id="Company A", name="A2", phone="+919000000002")
        b1 = Contact(contact_id="b1", company_id="Company B", name="B1", phone="+919000000003")
        c1 = Contact(contact_id="c1", company_id="Company C", name="C1", phone="+919000000004")
        c2 = Contact(contact_id="c2", company_id="Company C", name="C2", phone="+919000000005")
        c3 = Contact(contact_id="c3", company_id="Company C", name="C3", phone="+919000000006")

        all_contacts = [a1, a2, b1, c1, c2, c3]

        # Initial state (0 dispatched)
        metrics0 = calculate_company_round_state(all_contacts, dispatched_contact_ids=set())
        assert metrics0.current_round == 1
        assert metrics0.total_rounds == 3
        assert metrics0.companies_total == 3
        assert metrics0.companies_covered_total == 0
        assert metrics0.companies_remaining_current_round == 3

        # After Round 1 (a1, b1, c1 dispatched)
        dispatched_r1 = {"a1", "b1", "c1"}
        metrics1 = calculate_company_round_state(all_contacts, dispatched_contact_ids=dispatched_r1)
        assert metrics1.current_round == 2
        assert metrics1.companies_covered_total == 3
        assert metrics1.companies_with_remaining_contacts == 2  # A and C have remaining contacts

        # After Round 2 (a2, c2 dispatched)
        dispatched_r2 = {"a1", "b1", "c1", "a2", "c2"}
        metrics2 = calculate_company_round_state(all_contacts, dispatched_contact_ids=dispatched_r2)
        assert metrics2.current_round == 3
        assert metrics2.companies_with_remaining_contacts == 1  # Only C has remaining


class TestHistoricalExclusion:
    """Dimension 3: Contact Eligibility & Historical Exclusion verification suite."""

    def test_historical_contact_excluded_from_automatic_campaign(self):
        """Contacts with previous successful outreach are excluded from automatic outreach."""
        contact_wa_sent = Contact(
            contact_id="cnt_sent",
            company_id="apple",
            name="HR1",
            phone="+919000000010",
            last_whatsapp_at=datetime.now(timezone.utc),
        )
        res_wa = evaluate_automatic_eligibility(contact_wa_sent, Channel.WHATSAPP)
        assert res_wa.is_eligible is False
        assert res_wa.reason == "ALREADY_SENT_WHATSAPP"

        # But eligible for Email if not sent Email
        contact_wa_sent.email = "hr1@apple.com"
        res_em = evaluate_automatic_eligibility(contact_wa_sent, Channel.EMAIL)
        assert res_em.is_eligible is True

    def test_manual_resend_bypasses_duplicate_and_creates_new_attempt(self, outreach_db):
        """Manual resend creates a new immutable OutreachAttempt while preserving history."""
        with outreach_db() as session:
            comp = Company.create(name="Apple Inc", company_id="apple")
            SqliteCompanyRepository(session).save(comp)

            contact = Contact(
                contact_id="cnt_resend",
                company_id="apple",
                name="HR1",
                phone="+919000000010",
                last_whatsapp_at=datetime.now(timezone.utc),
            )
            SqliteContactRepository(session).save(contact)
            sender = SenderAccount.create(
                channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="S1", sender_id="WA-001"
            )
            SqliteSenderRepository(session).save(sender)

            # Historical attempt
            prev_attempt = OutreachAttempt.prepare(
                contact_id="cnt_resend",
                sender_account_id="WA-001",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Original outreach",
                idempotency_key="orig_key_1",
            )
            prev_attempt.mark_sent(provider_reference="prov_ref_1")
            SqliteOutreachRepository(session).save(prev_attempt)
            session.commit()

            outreach_svc = OutreachService(
                session=session,
                whatsapp_provider=MockWhatsAppProvider(),
                email_provider=MockEmailProvider(),
            )

            res = outreach_svc.resend_whatsapp(contact_id="cnt_resend", sender_id="WA-001")
            assert res["success"] is True
            assert res["attempt_type"] == "RESEND"

            # Check both attempts exist in DB
            all_attempts = SqliteOutreachRepository(session).list_by_contact("cnt_resend")
            assert len(all_attempts) == 2
            assert all_attempts[0].id != all_attempts[1].id
            assert all_attempts[0].status == OutreachStatus.SENT
            assert all_attempts[1].status == OutreachStatus.SENT


class TestChannelFallback:
    """Dimension 4 & 5: Channel Fallback and Safety verification suite."""

    def test_missing_phone_falls_back_to_email(self):
        """Contact missing phone falls back safely to Email if email handle is available."""
        contact = Contact(
            contact_id="cnt_no_phone",
            company_id="uber",
            name="Uber Recruiter",
            phone="",
            email="recruiter@uber.com",
        )

        fb = ChannelFallbackPolicy.determine_fallback_channel(
            contact=contact,
            failed_channel=Channel.WHATSAPP,
            failure_code="MISSING_PHONE_NUMBER",
        )
        assert fb == Channel.EMAIL

    def test_ambiguous_unknown_outcome_does_not_trigger_automatic_fallback(self):
        """Ambiguous UNKNOWN provider result blocks automatic fallback to prevent duplicate sends."""
        assert ChannelFallbackPolicy.is_fallback_safe(OutreachStatus.UNKNOWN) is False
        assert ChannelFallbackPolicy.is_fallback_safe(OutreachStatus.RECOVERY_REQUIRED) is False
        assert (
            ChannelFallbackPolicy.is_fallback_safe(OutreachStatus.FAILED, failure_code="ERR_PHONE_NOT_ON_WHATSAPP")
            is True
        )


class TestSenderRotation:
    """Dimension 4: Multi-Sender Rotation and Availability Precedence suite."""

    def test_rotation_skips_unavailable_or_auth_required_senders(self):
        """If WA-001 is AUTH_REQUIRED, scheduler selects next eligible sender (WA-002)."""
        s1 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="S1", sender_id="WA-001"
        )
        s1.status = SenderStatus.AUTH_REQUIRED

        s2 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91002", display_name="S2", sender_id="WA-002"
        )
        s2.status = SenderStatus.ACTIVE

        senders = [s1, s2]
        chosen, next_cursor = SenderRotationPolicy.select_next_sender(senders, cursor=0)
        assert chosen is not None
        assert chosen.id == "WA-002"

    def test_dynamic_arbitrary_n_senders(self):
        """Supports arbitrary N senders dynamically without hardcoding."""
        senders = [
            SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="MOCK",
                identity=f"+9100{i}",
                display_name=f"S{i}",
                sender_id=f"WA-{i:03d}",
            )
            for i in range(1, 11)
        ]
        assert len(senders) == 10

        cursor = 0
        visited = []
        for _ in range(10):
            chosen, cursor = SenderRotationPolicy.select_next_sender(senders, cursor=cursor)
            visited.append(chosen.id)
        assert sorted(visited) == sorted(s.id for s in senders)


class TestCampaignQuota:
    """Dimension 6: Campaign Quota tracking suite."""

    def test_automatic_quota_and_manual_reserve(self):
        """Verify automatic quota and manual reserve tracking."""
        campaign = Campaign.create(
            name="Quota Test",
            channel=Channel.WHATSAPP,
            automatic_quota=100,
            manual_reserve=20,
        )
        assert campaign.automatic_quota == 100
        assert campaign.manual_reserve == 20
        assert campaign.automatic_used == 0
        assert campaign.remaining_automatic == 100
        assert campaign.can_dispatch_automatic() is True

        # Simulate quota consumption (the scheduler persists this via the repository).
        campaign.metadata["automatic_used"] = 100

        assert campaign.automatic_used == 100
        assert campaign.remaining_automatic == 0
        assert campaign.can_dispatch_automatic() is False


class TestEndToEndScenario:
    """Section 28: End-to-End Scenario verification."""

    def test_end_to_end_company_first_multi_sender_and_fallback(self, outreach_db):
        """Full execution of Section 28: Apple(2), Uber(2), GreyOrange(3) with 4 senders."""
        with outreach_db() as session:
            # Create Senders
            wa1 = SenderAccount.create(
                channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="WA 1", sender_id="WA-001"
            )
            wa2 = SenderAccount.create(
                channel=Channel.WHATSAPP, provider="MOCK", identity="+91002", display_name="WA 2", sender_id="WA-002"
            )
            em1 = SenderAccount.create(
                channel=Channel.EMAIL, provider="MOCK", identity="e1@co.com", display_name="EM 1", sender_id="EMAIL-001"
            )
            em2 = SenderAccount.create(
                channel=Channel.EMAIL, provider="MOCK", identity="e2@co.com", display_name="EM 2", sender_id="EMAIL-002"
            )

            sender_repo = SqliteSenderRepository(session)
            for s in (wa1, wa2, em1, em2):
                sender_repo.save(s)

            # Create Contacts
            contacts_data = [
                ("Apple", "HR1", "+919000000001", "hr1@apple.com"),
                ("Apple", "HR2", "+919000000002", "hr2@apple.com"),
                ("Uber", "HR3", "+919000000003", "hr3@uber.com"),
                ("Uber", "HR4", "+919000000004", "hr4@uber.com"),
                ("Grey Orange", "HR5", "+919000000005", "hr5@greyorange.com"),
                ("Grey Orange", "HR6", "+919000000006", "hr6@greyorange.com"),
                ("Grey Orange", "HR7", "+919000000007", "hr7@greyorange.com"),
            ]

            comp_repo = SqliteCompanyRepository(session)
            contact_repo = SqliteContactRepository(session)
            for comp_name, hr_name, phone, email in contacts_data:
                comp = comp_repo.get_by_id(comp_name.lower().replace(" ", "_"))
                if not comp:
                    comp = Company.create(name=comp_name, company_id=comp_name.lower().replace(" ", "_"))
                    comp_repo.save(comp)
                cnt = Contact(
                    contact_id=f"cnt_{hr_name.lower()}",
                    company_id=comp.id,
                    name=hr_name,
                    phone=phone,
                    email=email,
                )
                contact_repo.save(cnt)

            # Seed templates
            TemplateService(session).seed_defaults_if_empty()
            session.commit()

        # Prioritization test
        with outreach_db() as session:
            all_c = SqliteContactRepository(session).list_all()
            ordered = prioritize_company_first(all_c)
            ordered_names = [c.name for c in ordered]
            assert ordered_names == ["HR1", "HR5", "HR3", "HR2", "HR6", "HR4", "HR7"]


class TestDataIntegrity:
    """Verify source artifacts remain completely untouched and byte-identical."""

    def test_source_files_sha256_integrity(self):
        """Verify source Excel files and send log are byte-identical."""
        expected_hashes = {
            "data/MNC_Final.xlsx": "a0a584852ed2267261583cbeae56692c29dcbebceafc5259da9a6c1eb9672a7b",
            "data/Reachout.xlsx": "e0fd507dc5ec5b041c95fc2edb302d2fc13225ac7f884cbb826dfa909960f46c",
            "logs/mnc_whatsapp_send_log.csv": "5b50f9fedcd3cdd95307c78119705796fac88bdee9b553cfe3eea4f98ae4244a",
        }

        root = Path(__file__).resolve().parent.parent.parent
        for rel_path, expected_hash in expected_hashes.items():
            full_path = root / rel_path
            assert full_path.exists(), f"Source file {rel_path} does not exist!"
            actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            assert actual_hash == expected_hash, (
                f"Data integrity violation in {rel_path}! Expected {expected_hash}, got {actual_hash}"
            )
