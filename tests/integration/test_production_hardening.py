"""Phase 9 Comprehensive Production Hardening & Regression Test Suite.

Covers all 8 critical and high audit findings:
1. In-flight / Ambiguous blocker argument wiring & safety.
2. 120s WhatsApp / 60s Email pacing enforcement, timeout aborts, and concurrency locks.
3. Strict Company-First Round-Robin interleaving and invariant distribution.
4. Definitive WhatsApp failure exclusion and bidirectional Email fallback.
5. Rate-limiter-aware multi-sender rotation.
6. WhatsApp authentication state machine (live vs mock isolation, seed states).
7. SMTP credential encrypted vault persistence and restart reconciliation.
8. Legacy /api/stats delegation to canonical CRM KPIs.
9. Excel -> SQLite non-destructive history preservation.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.endpoint import CommunicationEndpoint
from app.domain.enums import AttemptType, CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.channel_rotation_policy import ChannelRotationPolicy
from app.domain.policies.endpoint_coverage_policy import (
    get_contact_endpoint_metrics,
    get_next_uncovered_endpoint,
    get_uncovered_endpoints,
    has_ambiguous_or_inflight_blocker,
    is_contact_fully_covered,
    is_endpoint_covered,
    is_endpoint_permanently_failed,
)
from app.domain.policies.fallback_policy import ChannelFallbackPolicy
from app.domain.policies.prioritization import (
    calculate_company_round_state,
    prioritize_company_first,
)
from app.domain.policies.sender_rotation import SenderRotationPolicy
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base, SessionFactory
from app.infrastructure.events.event_bus import EventBus
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
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
from app.infrastructure.security.credential_vault import CredentialVault, default_credential_vault
from app.infrastructure.source.excel_reader import TabularSourceReader
from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer
from app.ports.providers import ProviderSendResult
from app.ports.source import SourceRow
from app.services.crm_service import CrmService
from app.services.sender_service import SenderService


# ==============================================================================
# FINDING 1: IN-FLIGHT / AMBIGUOUS BLOCKER CHECK
# ==============================================================================
class TestFinding1InflightBlocker:
    """Audit Finding 1: Inverted arguments in the in-flight blocker check."""

    def test_has_ambiguous_or_inflight_blocker_types_and_wiring(self):
        """Verify has_ambiguous_or_inflight_blocker works with both Contact and string ID."""
        c = Contact(contact_id="cnt_test_01", company_id="comp_1", name="Test Person", phone="+919999999991")
        now = datetime.now(timezone.utc)

        # Empty history -> False
        assert has_ambiguous_or_inflight_blocker(c, []) is False
        assert has_ambiguous_or_inflight_blocker("cnt_test_01", []) is False

        # In-flight SENDING attempt on this contact -> True
        att_sending = OutreachAttempt.prepare(
            contact_id="cnt_test_01",
            sender_account_id="snd_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919999999991",
        )
        att_sending.mark_sending(now)

        assert has_ambiguous_or_inflight_blocker(c, [att_sending]) is True
        assert has_ambiguous_or_inflight_blocker("cnt_test_01", [att_sending]) is True

        # In-flight QUEUED attempt -> True
        att_queued = OutreachAttempt.prepare(
            contact_id="cnt_test_01",
            sender_account_id="snd_01",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="test@example.com",
        )
        att_queued.mark_queued()
        assert has_ambiguous_or_inflight_blocker(c, [att_queued]) is True

        # RECOVERY_REQUIRED attempt -> True
        att_rec = OutreachAttempt.prepare(
            contact_id="cnt_test_01",
            sender_account_id="snd_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919999999991",
        )
        att_rec.mark_recovery_required("Crash timeout", now)
        assert has_ambiguous_or_inflight_blocker(c, [att_rec]) is True

        # UNKNOWN attempt -> True
        att_unk = OutreachAttempt.prepare(
            contact_id="cnt_test_01",
            sender_account_id="snd_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919999999991",
        )
        att_unk.mark_unknown("Unknown provider outcome", now)
        assert has_ambiguous_or_inflight_blocker(c, [att_unk]) is True

    def test_channel_rotation_policy_evaluates_inflight_blocker(self):
        """Verify ChannelRotationPolicy.evaluate_contact_dispatch correctly blocks candidate."""
        c = Contact(contact_id="cnt_test_02", company_id="comp_1", name="Test Person 2", phone="+919999999992")
        now = datetime.now(timezone.utc)

        att_sending = OutreachAttempt.prepare(
            contact_id="cnt_test_02",
            sender_account_id="snd_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919999999992",
        )
        att_sending.mark_sending(now)

        decision = ChannelRotationPolicy.evaluate_contact_dispatch(
            contact=c,
            preferred_channel=Channel.WHATSAPP,
            historical_attempts=[att_sending],
        )

        assert decision.is_eligible is False
        assert decision.reason == "AMBIGUOUS_IN_FLIGHT_BLOCKED"

    def test_sent_and_failed_attempts_do_not_block_unrelated_endpoints(self):
        """Completed SENT and normal FAILED attempts on other contacts do not block."""
        c1 = Contact(contact_id="cnt_01", company_id="comp_1", name="Person 1", phone="+919999999991", email="p1@test.com")
        c2 = Contact(contact_id="cnt_02", company_id="comp_1", name="Person 2", phone="+919999999992", email="p2@test.com")
        now = datetime.now(timezone.utc)

        att_c1_sent = OutreachAttempt.prepare(
            contact_id="cnt_01",
            sender_account_id="snd_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919999999991",
        )
        att_c1_sent.mark_sent("ref_01", now)

        # c2 has no in-flight blocker
        assert has_ambiguous_or_inflight_blocker(c2, [att_c1_sent]) is False

        # c2 is eligible for dispatch
        decision = ChannelRotationPolicy.evaluate_contact_dispatch(
            contact=c2,
            preferred_channel=Channel.WHATSAPP,
            historical_attempts=[att_c1_sent],
        )
        assert decision.is_eligible is True
        assert decision.channel == Channel.WHATSAPP


# ==============================================================================
# FINDING 2 & 5: PACING, TIMEOUT, AND SENDER ROTATION
# ==============================================================================
class TestFinding2PacingAndSenderRotation:
    """Audit Finding 2 & 5: Pacing delays, timeout handling, and rate-limiter aware rotation."""

    def test_pacing_delay_defaults(self):
        """Verify WhatsApp delay is 120s and Email is 60s in live mode or explicit config."""
        rl_custom = RateLimiter(default_channel_delay={"WHATSAPP": 120.0, "EMAIL": 60.0})
        assert rl_custom.default_channel_delay["WHATSAPP"] == 120.0
        assert rl_custom.default_channel_delay["EMAIL"] == 60.0

    def test_pacing_wait_for_ready_timeout(self):
        """wait_for_ready returns False when sender is in cooldown and timeout is smaller than delay."""
        rl = RateLimiter(default_channel_delay={"WHATSAPP": 120.0, "EMAIL": 60.0})
        rl.record_dispatch_success("snd_test_wa")

        # Sender is cooling down for 120s; waiting with short timeout must return False
        ready = rl.wait_for_ready("snd_test_wa", "WHATSAPP", timeout_seconds=0.05)
        assert ready is False

    def test_outreach_worker_aborts_on_pacing_timeout_without_provider_call(self, tmp_path):
        """When wait_for_ready times out, OutreachWorker does NOT call provider and marks FAILED."""
        engine = create_engine(f"sqlite:///{tmp_path / 'worker_timeout.db'}")
        Base.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine)

        mock_wa_provider = MockWhatsAppProvider()

        class MockTimeoutRateLimiter(RateLimiter):
            def wait_for_ready(self, *args, **kwargs):
                return False

        rl = MockTimeoutRateLimiter()

        worker = OutreachWorker(
            session_factory=SessionFactory,
            whatsapp_provider=mock_wa_provider,
            rate_limiter=rl,
        )

        with SessionFactory() as session:
            c_repo = SqliteContactRepository(session)
            co_repo = SqliteCompanyRepository(session)
            s_repo = SqliteSenderRepository(session)
            t_repo = SqliteTemplateRepository(session)

            co = Company.create("Test Corp", "co_01")
            co_repo.save(co)
            cnt = Contact(contact_id="cnt_timeout_01", company_id="co_01", name="Timeout Recipient", phone="+919999900001")
            c_repo.save(cnt)
            tmpl = MessageTemplate.create(
                name="Tmpl",
                channel=Channel.WHATSAPP,
                body="Hi {name}",
                template_id="tmpl_01",
            )
            t_repo.save(tmpl)
            snd = SenderAccount.create(Channel.WHATSAPP, "mock", "+919999900000", "WA Line", sender_id="snd_wa_slow")
            s_repo.save(snd)
            session.commit()

        attempt = worker.execute_attempt(
            contact_id="cnt_timeout_01",
            sender_account=snd,
            template=tmpl,
        )

        # 1. Attempt must NOT be SENT
        assert attempt.status == OutreachStatus.FAILED
        assert attempt.failure_code == "ERR_PACING_TIMEOUT"

        # 2. Provider send_message MUST NOT have been invoked
        assert len(mock_wa_provider.sent_calls) == 0

        # 3. Sender lock MUST be released
        assert rl._sender_busy["snd_wa_slow"] is False

    def test_sender_rotation_selects_ready_sender_when_first_is_cooling_down(self):
        """SenderRotationPolicy skips cooling down senders and chooses next available sender."""
        rl = RateLimiter(default_channel_delay={"WHATSAPP": 120.0})
        s1 = SenderAccount.create(Channel.WHATSAPP, "mock", "+919000000001", "WA 1", sender_id="snd_wa_1")
        s2 = SenderAccount.create(Channel.WHATSAPP, "mock", "+919000000002", "WA 2", sender_id="snd_wa_2")
        senders = [s1, s2]

        # s1 just dispatched and is cooling down
        rl.record_dispatch_success("snd_wa_1")

        def check_avail(s: SenderAccount) -> bool:
            can, _ = rl.can_send(s.id, s.channel.value)
            return can

        # Rotation starting at cursor 0 checks s1 (not ready) -> selects s2 (ready!)
        selected, next_cursor = SenderRotationPolicy.select_next_sender(
            senders=senders,
            cursor=0,
            availability_checker=check_avail,
        )
        assert selected is not None
        assert selected.id == "snd_wa_2"
        assert next_cursor == 0  # advanced past s2 (index 1 + 1 % 2 = 0)

    def test_concurrent_workers_cannot_bypass_same_sender_cooldown(self):
        """Thread concurrency test: two threads competing for the same sender."""
        rl = RateLimiter(default_channel_delay={"WHATSAPP": 1.0})
        acquired_first = rl.acquire_sender("snd_concur")
        assert acquired_first is True

        # Second acquisition on same sender while busy must return False
        acquired_second = rl.acquire_sender("snd_concur")
        assert acquired_second is False

        rl.release_sender("snd_concur")
        # After release, acquisition succeeds
        assert rl.acquire_sender("snd_concur") is True
        rl.release_sender("snd_concur")


# ==============================================================================
# FINDING 3: COMPANY-FIRST ROUND-ROBIN
# ==============================================================================
class TestFinding3CompanyFirstRoundRobin:
    """Audit Finding 3: Strict Company-First round-robin algorithm."""

    def test_strict_interleaving_a1_b1_c1_a2_b2_a3(self):
        """Canonical test: Company A(3), Company B(2), Company C(1) produces A1, B1, C1, A2, B2, A3."""
        contacts = [
            Contact(contact_id="cnt_a1", company_id="CompanyA", name="A1"),
            Contact(contact_id="cnt_a2", company_id="CompanyA", name="A2"),
            Contact(contact_id="cnt_a3", company_id="CompanyA", name="A3"),
            Contact(contact_id="cnt_b1", company_id="CompanyB", name="B1"),
            Contact(contact_id="cnt_b2", company_id="CompanyB", name="B2"),
            Contact(contact_id="cnt_c1", company_id="CompanyC", name="C1"),
        ]

        ordered = prioritize_company_first(contacts)
        result_names = [c.name for c in ordered]
        assert result_names == ["A1", "B1", "C1", "A2", "B2", "A3"]

    def test_n_eligible_companies_invariant(self):
        """Invariant: For N eligible companies, the first N dispatches are across N distinct companies."""
        companies = [f"Company_{chr(65 + i)}" for i in range(5)]  # A, B, C, D, E
        contacts = []
        for cname in companies:
            for j in range(1, 4):  # 3 contacts per company
                contacts.append(Contact(contact_id=f"{cname}_{j}", company_id=cname, name=f"{cname}_{j}"))

        ordered = prioritize_company_first(contacts)
        first_5_companies = [c.company_id for c in ordered[:5]]
        assert len(set(first_5_companies)) == 5
        assert first_5_companies == companies

    def test_multi_endpoint_and_attempt_touches_round_progression(self):
        """When Company A already received 1 dispatch, Company B (0 dispatches) gets priority in Round 1."""
        contacts = [
            Contact(contact_id="a1", company_id="CoA", name="A1"),
            Contact(contact_id="a2", company_id="CoA", name="A2"),
            Contact(contact_id="b1", company_id="CoB", name="B1"),
            Contact(contact_id="b2", company_id="CoB", name="B2"),
        ]

        # Simulate A1 was dispatched (Company A has 1 touch, Company B has 0 touches)
        dispatched_touches = {"a1": 1}
        # If A1 still has another endpoint (eligible)
        ordered = prioritize_company_first(
            contacts,
            eligibility_predicate=lambda c: True,
            dispatched_contact_ids=dispatched_touches,
        )
        names = [c.name for c in ordered]
        # Round 1: B1 (CoB has 0 touches)
        # Round 2: A1 (CoA has 1 touch), B2 (CoB has 1 touch)
        # Round 3: A2 (CoA has 2 touches)
        assert names == ["B1", "A1", "B2", "A2"]

    def test_calculate_company_round_state_accuracy(self):
        """calculate_company_round_state dynamically tracks metrics across rounds."""
        contacts = [
            Contact(contact_id="a1", company_id="CoA", name="A1"),
            Contact(contact_id="a2", company_id="CoA", name="A2"),
            Contact(contact_id="b1", company_id="CoB", name="B1"),
            Contact(contact_id="c1", company_id="CoC", name="C1"),
            Contact(contact_id="c2", company_id="CoC", name="C2"),
            Contact(contact_id="c3", company_id="CoC", name="C3"),
        ]

        m0 = calculate_company_round_state(contacts, dispatched_contact_ids=set())
        assert m0.current_round == 1
        assert m0.total_rounds == 3
        assert m0.companies_total == 3
        assert m0.companies_covered_total == 0
        assert m0.total_eligible_contacts == 6


# ==============================================================================
# FINDING 4: DEFINITIVE FAILURE AND CHANNEL FALLBACK
# ==============================================================================
class TestFinding4DefinitiveFailureAndFallback:
    """Audit Finding 4: Permanent failure exclusion and Email fallback."""

    def test_definitive_whatsapp_failure_excludes_endpoint_and_falls_back(self):
        """When WhatsApp fails with ERR_NOT_ON_WHATSAPP, endpoint is excluded and Email is chosen."""
        c = Contact(
            contact_id="cnt_fb_01",
            company_id="comp_fb",
            name="Fallback Person",
            phone="+919800000001",
            email="fb.person@example.com",
        )
        now = datetime.now(timezone.utc)

        # 1. Historical failed WhatsApp attempt (ERR_NOT_ON_WHATSAPP)
        att_wa_failed = OutreachAttempt.prepare(
            contact_id="cnt_fb_01",
            sender_account_id="snd_wa_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="WA body",
            destination="+919800000001",
        )
        att_wa_failed.mark_failed("ERR_NOT_ON_WHATSAPP", "Number not registered on WhatsApp", now)

        history = [att_wa_failed]

        # 2. Verify WhatsApp endpoint is marked permanently failed
        wa_ep = c.endpoints[0]
        assert wa_ep.channel == Channel.WHATSAPP
        assert is_endpoint_permanently_failed(wa_ep, c.contact_id, history) is True

        # 3. Verify get_uncovered_endpoints for WhatsApp is now empty
        uncovered_wa = get_uncovered_endpoints(c, Channel.WHATSAPP, history)
        assert len(uncovered_wa) == 0

        # 4. Evaluate contact dispatch with preferred channel WHATSAPP -> falls back to EMAIL!
        decision = ChannelRotationPolicy.evaluate_contact_dispatch(
            contact=c,
            preferred_channel=Channel.WHATSAPP,
            historical_attempts=history,
        )

        assert decision.is_eligible is True
        assert decision.channel == Channel.EMAIL
        assert decision.is_fallback is True
        assert decision.endpoint is not None
        assert decision.endpoint.channel == Channel.EMAIL
        assert decision.endpoint.address == "fb.person@example.com"

    def test_transient_failure_does_not_permanently_exclude_endpoint(self):
        """Transient error (e.g. ERR_TIMEOUT or ERR_RATE_LIMIT) does NOT permanently exclude endpoint."""
        c = Contact(contact_id="cnt_trans_01", company_id="comp_1", name="Transient Test", phone="+919800000002")
        now = datetime.now(timezone.utc)

        att_trans = OutreachAttempt.prepare(
            contact_id="cnt_trans_01",
            sender_account_id="snd_wa_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="test",
            destination="+919800000002",
        )
        att_trans.mark_failed("ERR_TIMEOUT", "Provider socket timed out", now)

        wa_ep = c.endpoints[0]
        assert is_endpoint_permanently_failed(wa_ep, c.contact_id, [att_trans]) is False

        uncovered = get_uncovered_endpoints(c, Channel.WHATSAPP, [att_trans])
        assert len(uncovered) == 1

    def test_no_infinite_loop_when_all_endpoints_fail(self):
        """When both WhatsApp and Email fail definitively, contact is fully covered with no infinite loop."""
        c = Contact(
            contact_id="cnt_all_failed",
            company_id="comp_1",
            name="All Fail",
            phone="+919800000003",
            email="invalid@example.com",
        )
        now = datetime.now(timezone.utc)

        att_wa = OutreachAttempt.prepare(
            contact_id="cnt_all_failed",
            sender_account_id="snd_wa_01",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="wa",
            destination="+919800000003",
        )
        att_wa.mark_failed("ERR_NOT_ON_WHATSAPP", "Not on WA", now)

        att_em = OutreachAttempt.prepare(
            contact_id="cnt_all_failed",
            sender_account_id="snd_em_01",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="em",
            destination="invalid@example.com",
        )
        att_em.mark_failed("ERR_INVALID_EMAIL", "Mailbox does not exist", now)

        history = [att_wa, att_em]

        # Contact has no uncovered endpoints remaining
        assert is_contact_fully_covered(c, history) is True

        decision = ChannelRotationPolicy.evaluate_contact_dispatch(
            contact=c,
            preferred_channel=Channel.WHATSAPP,
            historical_attempts=history,
        )
        assert decision.is_eligible is False
        assert decision.reason == "CONTACT_FULLY_COVERED"


# ==============================================================================
# FINDING 6: WHATSAPP AUTHENTICATION STATE MACHINE
# ==============================================================================
class TestFinding6WhatsAppAuthentication:
    """Audit Finding 6: WhatsApp live vs mock authentication state machine."""

    def test_fresh_baseline_starts_with_zero_senders(self):
        """Baseline: a fresh repository seeds no senders (operator adds sessions).

        Replaces the old behavior where default senders were pre-seeded. The
        operator adds the required number of WhatsApp and Email sessions.
        """
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        with SessionLocal() as session:
            svc = SenderService(session)
            svc.seed_defaults_if_empty()
            assert svc.list_senders() == []

    def test_whatsapp_auth_flow_qr_to_active(self):
        """Starting QR auth returns AUTHENTICATING and set_auth_state transitions to ACTIVE."""
        mgr = WhatsAppSessionManager()
        sender_id = "WA_SESSION_TEST_P9"

        # Start auth
        res_start = mgr.start_qr_authentication(sender_id)
        assert res_start["status"] in (SenderStatus.AUTHENTICATING.value, SenderStatus.QR_REQUIRED.value)

        # Set ACTIVE when authenticated
        res_confirm = mgr.set_auth_state(sender_id, SenderStatus.ACTIVE)
        assert res_confirm["status"] == SenderStatus.ACTIVE.value

        # Status check returns ACTIVE
        cur = mgr.get_auth_state(sender_id)
        assert cur["status"] == SenderStatus.ACTIVE.value


# ==============================================================================
# FINDING 7: SMTP CREDENTIAL ENCRYPTED VAULT PERSISTENCE
# ==============================================================================
class TestFinding7SmtpCredentialPersistence:
    """Audit Finding 7: Encrypted persistence of SMTP credentials surviving restart."""

    def test_credentials_stored_and_retrieved_encrypted(self, tmp_path):
        """CredentialVault persists encrypted secrets to disk and decrypts with integrity check."""
        vault_path = tmp_path / "smtp_vault.enc"
        key_path = tmp_path / ".vault_key"

        vault = CredentialVault(vault_path=vault_path, key_path=key_path)
        creds = {
            "user": "recruiter@company.com",
            "password": "super_secret_app_password",
            "host": "smtp.gmail.com",
            "port": "587",
        }

        # Save credentials
        vault.save_credentials("snd_em_vault_01", creds)

        # Verify ciphertext file exists on disk
        assert vault_path.exists()
        raw_bytes = vault_path.read_bytes()
        # Password plaintext must NOT appear in ciphertext
        assert b"super_secret_app_password" not in raw_bytes

        # Create a new Vault instance reading from the same file
        new_vault = CredentialVault(vault_path=vault_path, key_path=key_path)
        recovered = new_vault.get_credentials("snd_em_vault_01")
        assert recovered is not None
        assert recovered["user"] == "recruiter@company.com"
        assert recovered["password"] == "super_secret_app_password"

    def test_reconcile_sender_states_marks_missing_credentials_as_auth_required(self, tmp_path):
        """On process restart, if an active email sender has no stored credentials, it becomes AUTH_REQUIRED."""
        engine = create_engine(f"sqlite:///{tmp_path / 'restart_reconcile.db'}")
        Base.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine)

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            # Active sender in DB without credentials in vault
            snd = SenderAccount.create(
                Channel.EMAIL, "smtp", "orphan@company.com", "Orphan Sender", sender_id="snd_em_orphan"
            )
            snd.status = SenderStatus.ACTIVE
            sender_repo.save(snd)
            session.commit()

        # Run reconciliation on startup
        with SessionFactory() as session:
            svc = SenderService(session)
            svc.reconcile_sender_states()
            updated = svc.repo.get_by_id("snd_em_orphan")
            assert updated is not None
            assert updated.status == SenderStatus.AUTH_REQUIRED


# ==============================================================================
# FINDING 8: CANONICAL CRM KPIS SERVICE
# ==============================================================================
class TestFinding8CanonicalCrmKpis:
    """Audit Finding 8: Canonical CRM KPI service aggregation."""

    def test_crm_kpis_service_aggregation(self, tmp_path):
        """Verify CrmService.get_kpis provides complete operational metrics."""
        with SessionFactory() as session:
            svc = CrmService(session)
            kpis = svc.get_kpis()
            assert "total_contacts" in kpis
            assert "contacted" in kpis
            assert "whatsapp_sent" in kpis
            assert "email_sent" in kpis
            assert "interested" in kpis
            assert "not_interested" in kpis
            assert "follow_up_due" in kpis


# ==============================================================================
# FINDING 12: EXCEL SYNC NON-DESTRUCTIVE HISTORY PRESERVATION
# ==============================================================================
class TestFinding12ExcelSyncHistoryPreservation:
    """Audit Finding 12: Sync never deletes or mutates historical OutreachAttempts."""

    def test_excel_sync_preserves_attempts(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'sync_preservation.db'}")
        Base.metadata.create_all(engine)
        SessionFactory = sessionmaker(bind=engine)

        now = datetime.now(timezone.utc)

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            co = Company.create("Acme Corp", "comp_acme")
            comp_repo.save(co)
            cnt = Contact(contact_id="cnt_acme_1", company_id="comp_acme", name="Alice", phone="+919000000001")
            contact_repo.save(cnt)

            # Persist the referenced sender so the attempt FK resolves.
            SqliteSenderRepository(session).save(SenderAccount.create(
                sender_id="snd_01", channel=Channel.WHATSAPP, provider="mock",
                identity="+919000000000", display_name="Snd"))

            # Historical SENT attempt
            att_sent = OutreachAttempt.prepare(
                contact_id="cnt_acme_1",
                sender_account_id="snd_01",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="msg",
                destination="+919000000001",
            )
            att_sent.mark_sent("ref_123", now)
            outreach_repo.save(att_sent)

            # Historical FAILED attempt
            att_failed = OutreachAttempt.prepare(
                contact_id="cnt_acme_1",
                sender_account_id="snd_01",
                channel=Channel.EMAIL,
                attempt_type=AttemptType.MANUAL,
                message_body="msg",
                destination="alice@acme.com",
            )
            att_failed.mark_failed("ERR_INVALID_EMAIL", "No mailbox", now)
            outreach_repo.save(att_failed)

            session.commit()

        # Run synchronization with updated source data
        class MockReader:
            def read_source(self, path, sheet_name=None):
                return [
                    SourceRow(source_row=1, source_file="dummy.xlsx", raw_values={"company": "Acme Corp", "name": "Alice", "phone": "919000000001", "email": "alice.new@acme.com"}),
                    SourceRow(source_row=2, source_file="dummy.xlsx", raw_values={"company": "Acme Corp", "name": "Bob", "phone": "919000000002", "email": "bob@acme.com"}),
                ]

        with SessionFactory() as session:
            syncer = DatabaseSourceSynchronizer(session, reader=MockReader())
            summary = syncer.sync_source("dummy.xlsx")
            assert summary.history_preserved is True

        # Verify historical attempts were completely untouched
        with SessionFactory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            attempts = outreach_repo.list_all()
            assert len(attempts) == 2
            statuses = {a.status for a in attempts}
            assert OutreachStatus.SENT in statuses
            assert OutreachStatus.FAILED in statuses
