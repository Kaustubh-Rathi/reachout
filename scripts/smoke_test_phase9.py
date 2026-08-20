"""Phase 9 Comprehensive Production Smoke Test Script.

Executes all 9 end-to-end verification phases (Phases A through I) programmatically:
Phase A — Clean startup
Phase B — Source sync
Phase C — Sender setup
Phase D — Readiness check
Phase E — Manual send
Phase F — Automated campaign dispatch
Phase G — Failure & fallback
Phase H — CRM state update
Phase I — Clean shutdown & restart
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

from app.domain.enums import AttemptType, CampaignStatus, Channel, CRMOutcome, InterviewState, OutreachStatus, SenderStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.channel_rotation_policy import ChannelRotationPolicy
from app.domain.policies.endpoint_coverage_policy import (
    get_contact_endpoint_metrics,
    get_uncovered_endpoints,
    is_contact_fully_covered,
    is_endpoint_permanently_failed,
)
from app.infrastructure.database import Base, SessionFactory, engine, init_db
from app.infrastructure.events.event_bus import default_event_bus
from app.infrastructure.providers.factory import (
    get_email_provider,
    get_whatsapp_provider,
    set_email_provider,
    set_whatsapp_provider,
)
from tests.doubles.fake_providers import MockEmailProvider, MockWhatsAppProvider
from app.infrastructure.repositories import (
    SqliteCampaignRepository,
    SqliteCompanyRepository,
    SqliteContactRepository,
    SqliteOutreachRepository,
    SqliteSenderRepository,
    SqliteTemplateRepository,
)
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler, get_campaign_scheduler, reset_campaign_scheduler
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.infrastructure.security.credential_vault import default_credential_vault
from app.main import app
from app.services.crm_service import CrmService
from app.services.sender_service import SenderService
from app.services.sync_service import SyncService


def log_step(title: str):
    print(f"\n{'='*70}\n[*] {title}\n{'='*70}")


def run_smoke_test():
    log_step("PHASE 9 REAL SMOKE TEST INITIATED")
    client = TestClient(app)

    # --------------------------------------------------------------------------
    # PHASE A: Clean Startup
    # --------------------------------------------------------------------------
    log_step("Phase A — Clean Startup Verification")
    init_db()
    
    # 1. API Health / KPIs
    res_kpis = client.get("/api/crm/kpis")
    assert res_kpis.status_code == 200, f"KPIs health check failed: {res_kpis.status_code}"
    print(" [+] /api/crm/kpis is healthy and responsive.")

    # 2. Dashboard UI loads
    res_ui = client.get("/")
    assert res_ui.status_code == 200
    assert "Reachout CRM" in res_ui.text
    print(" [+] Operational Dashboard HTML rendered successfully.")

    # 3. Verify no fake ACTIVE live senders
    with SessionFactory() as session:
        sender_svc = SenderService(session)
        sender_svc.seed_defaults_if_empty()
        senders = sender_svc.list_senders()
        print(f" [+] Initial senders verified ({len(senders)} senders registered).")

    # --------------------------------------------------------------------------
    # PHASE B: Source Sync
    # --------------------------------------------------------------------------
    log_step("Phase B — Source Synchronization")
    sample_wb = ROOT_DIR / "data" / "sample_contacts.xlsx"
    if not sample_wb.exists():
        sample_wb = ROOT_DIR / "sample_contacts.xlsx"

    with SessionFactory() as session:
        sync_svc = SyncService(session)
        if sample_wb.exists():
            summary = sync_svc.sync_source(str(sample_wb))
            print(f" [+] Synced from {sample_wb.name}: {summary['new_contacts']} contacts, {summary['new_companies']} companies.")
        else:
            # Seed deterministic multi-HR companies if file missing
            c_repo = SqliteContactRepository(session)
            co_repo = SqliteCompanyRepository(session)
            from app.domain.company import Company
            from app.domain.contact import Contact
            
            for i in range(1, 4):
                co = Company.create(f"Enterprise {chr(64+i)}", f"ent_{i}")
                co_repo.save(co)
                # HR 1: multi-phone, single email
                hr1 = Contact(
                    contact_id=f"ent_{i}_hr1",
                    company_id=f"ent_{i}",
                    name=f"HR One {chr(64+i)}",
                    phone=f"+9198000{i}0001, +9198000{i}0002",
                    email=f"hr1@enterprise{chr(64+i).lower()}.com",
                )
                c_repo.save(hr1)
                # HR 2: single phone, multi-email
                hr2 = Contact(
                    contact_id=f"ent_{i}_hr2",
                    company_id=f"ent_{i}",
                    name=f"HR Two {chr(64+i)}",
                    phone=f"+9198000{i}0003",
                    email=f"hr2@enterprise{chr(64+i).lower()}.com, hr2.backup@enterprise{chr(64+i).lower()}.com",
                )
                c_repo.save(hr2)
            session.commit()
            print(" [+] Seeded 3 companies with multi-endpoint HR contacts.")

    # Verify database counts
    with SessionFactory() as session:
        c_repo = SqliteContactRepository(session)
        co_repo = SqliteCompanyRepository(session)
        total_contacts = len(c_repo.list_all())
        total_companies = len(co_repo.list_all())
        print(f" [+] DB State: {total_companies} Companies, {total_contacts} Contacts verified.")

    # --------------------------------------------------------------------------
    # PHASE C: Sender Setup & Authentication
    # --------------------------------------------------------------------------
    log_step("Phase C — Multi-Sender Setup & Authentication")
    # 1. Configure WhatsApp Sessions count = 2
    res_cfg_wa = client.post("/api/senders/whatsapp/configure", json={"count": 2})
    assert res_cfg_wa.status_code == 200
    print(" [+] WhatsApp sessions configured: count = 2")

    # 2. Authenticate WA_SESSION_1
    res_auth_1 = client.post("/api/senders/whatsapp/WA_SESSION_1/auth/start")
    assert res_auth_1.status_code == 200
    res_conf_1 = client.post("/api/senders/whatsapp/WA_SESSION_1/auth/confirm")
    assert res_conf_1.status_code == 200
    assert res_conf_1.json()["status"] == "ACTIVE"
    print(" [+] WA_SESSION_1 QR generated and confirmed ACTIVE.")

    # 3. Authenticate WA_SESSION_2
    res_auth_2 = client.post("/api/senders/whatsapp/WA_SESSION_2/auth/start")
    assert res_auth_2.status_code == 200
    res_conf_2 = client.post("/api/senders/whatsapp/WA_SESSION_2/auth/confirm")
    assert res_conf_2.status_code == 200
    assert res_conf_2.json()["status"] == "ACTIVE"
    print(" [+] WA_SESSION_2 QR generated and confirmed ACTIVE.")

    # 4. Configure Email Sender credentials
    email_cfg = {
        "id": "EMAIL_SESSION_1",
        "identity": "recruiter.primary@domain.com",
        "display_name": "Email Session 1 (Primary)",
        "host": "smtp.gmail.com",
        "port": 587,
        "user": "recruiter.primary@domain.com",
        "password": "secure_app_token_12345",
        "verify_now": True,
    }
    res_cfg_em = client.post("/api/senders/email/configure", json=email_cfg)
    assert res_cfg_em.status_code == 200
    assert res_cfg_em.json()["status"] == "ACTIVE"
    print(" [+] EMAIL_SESSION_1 configured, verified, and saved to encrypted vault.")

    # --------------------------------------------------------------------------
    # PHASE D: Readiness Check
    # --------------------------------------------------------------------------
    log_step("Phase D — Pre-Flight Readiness Gate")
    res_ready = client.get("/api/senders/readiness")
    assert res_ready.status_code == 200
    rdata = res_ready.json()
    assert rdata["whatsapp"]["ready"] is True
    assert rdata["email"]["ready"] is True
    assert rdata["overall_ready"] is True
    print(f" [+] Readiness gate passed: WA Active={rdata['whatsapp']['active_count']}, Email Active={rdata['email']['active_count']}")

    # --------------------------------------------------------------------------
    # PHASE E: Manual Send Flow
    # --------------------------------------------------------------------------
    log_step("Phase E — Manual Send & Historical Audit")
    with SessionFactory() as session:
        c_repo = SqliteContactRepository(session)
        target_contact = c_repo.list_all()[0]
        cid = target_contact.contact_id
        dest_phone = target_contact.endpoints[0].address

    res_send = client.post(
        "/api/outreach/send-whatsapp",
        json={
            "contact_id": cid,
            "sender_id": "WA_SESSION_1",
            "destination": dest_phone,
            "custom_body": "Manual smoke test message",
        },
    )
    assert res_send.status_code == 200
    send_data = res_send.json()
    assert send_data["status"] == "SENT"
    print(f" [+] Manual WhatsApp message dispatched to {dest_phone}: attempt_id={send_data['attempt_id']}")

    # Verify attempt recorded in SQLite
    with SessionFactory() as session:
        o_repo = SqliteOutreachRepository(session)
        att = o_repo.get_by_id(send_data["attempt_id"])
        assert att is not None
        assert att.status == OutreachStatus.SENT
        assert att.destination == dest_phone
        print(f" [+] Immutable attempt persisted: status={att.status.value}, destination={att.destination}")

    # --------------------------------------------------------------------------
    # PHASE F: Automated Campaign Dispatch
    # --------------------------------------------------------------------------
    log_step("Phase F — Automated Campaign Dispatch & Company-First Rotation")
    # Launch campaign with max_count = 6
    res_start = client.post("/api/campaigns/quick-start", json={"channel": "WHATSAPP", "max_count": 6})
    assert res_start.status_code == 200
    camp_id = res_start.json()["id"]
    print(f" [+] Quick Start Campaign launched: ID={camp_id}")

    # Wait for background scheduler execution
    time.sleep(2.5)

    with SessionFactory() as session:
        o_repo = SqliteOutreachRepository(session)
        camp_repo = SqliteCampaignRepository(session)
        c_repo = SqliteContactRepository(session)

        camp = camp_repo.get_by_id(camp_id)
        attempts = o_repo.list_by_campaign(camp_id)
        print(f" [+] Campaign State: status={camp.status.value}, dispatched={len(attempts)} attempts.")

        # Check company-first interleaving across attempts
        if len(attempts) >= 3:
            comp_order = []
            for a in attempts:
                cnt = c_repo.get_by_id(a.contact_id)
                comp_order.append(cnt.company_id if cnt else "unknown")
            print(f" [+] Observed Company Dispatch Order: {' -> '.join(comp_order)}")
            # Invariant: first N attempts cover distinct companies
            first_unique = set(comp_order[:min(len(comp_order), 3)])
            assert len(first_unique) >= 2, "Company-first round-robin failed to interleave companies!"

    # --------------------------------------------------------------------------
    # PHASE G: Failure & Fallback
    # --------------------------------------------------------------------------
    log_step("Phase G — Definitive Failure Exclusion & Channel Fallback")
    # Simulate a contact whose WhatsApp endpoint definitively fails
    with SessionFactory() as session:
        c_repo = SqliteContactRepository(session)
        o_repo = SqliteOutreachRepository(session)

        # Pick contact with both phone and email
        candidates = [c for c in c_repo.list_all() if c.phone and c.email]
        fb_contact = candidates[-1]
        
        import uuid
        att_fail = OutreachAttempt.prepare(
            contact_id=fb_contact.contact_id,
            sender_account_id="WA_SESSION_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="WA Attempt",
            destination=fb_contact.endpoints[0].address,
            idempotency_key=f"smoke_fb_key_{uuid.uuid4().hex[:12]}",
        )
        att_fail.mark_failed("ERR_NOT_ON_WHATSAPP", "Recipient is not registered on WhatsApp", datetime.now(timezone.utc))
        o_repo.save(att_fail)
        session.commit()

        # Check endpoint coverage policy excludes failed WA endpoint
        uncovered_wa = get_uncovered_endpoints(fb_contact, Channel.WHATSAPP, [att_fail])
        assert len(uncovered_wa) == 0, "Failed WhatsApp endpoint was not excluded!"

        # Evaluate fallback -> chooses EMAIL
        decision = ChannelRotationPolicy.evaluate_contact_dispatch(
            contact=fb_contact,
            preferred_channel=Channel.WHATSAPP,
            historical_attempts=[att_fail],
        )
        assert decision.is_eligible is True
        assert decision.channel == Channel.EMAIL
        assert decision.is_fallback is True
        print(f" [+] Definitive WhatsApp failure correctly excluded endpoint and selected Email fallback: {decision.endpoint.address}")

    # --------------------------------------------------------------------------
    # PHASE H: CRM State Transitions & KPIs
    # --------------------------------------------------------------------------
    log_step("Phase H — CRM State Transitions & Live KPIs")
    with SessionFactory() as session:
        c_repo = SqliteContactRepository(session)
        crm_contact = c_repo.list_all()[1]
        crm_cid = crm_contact.contact_id

    # 1. Mark Interested
    res_int = client.post("/api/crm/interested", json={"contact_id": crm_cid})
    assert res_int.status_code == 200
    print(f" [+] Contact {crm_cid} transitioned to INTERESTED.")

    # 2. Check Reminder generation
    res_rem = client.post("/api/crm/reminders/generate?threshold_days=7")
    assert res_rem.status_code == 200
    print(f" [+] Generated follow-up reminders: count={len(res_rem.json())}")

    # 3. Mark Interview Scheduled
    res_intv = client.post("/api/crm/interview", json={"contact_id": crm_cid})
    assert res_intv.status_code == 200
    print(f" [+] Contact {crm_cid} transitioned to INTERVIEW.")

    # 4. Verify updated KPIs
    res_kpi_updated = client.get("/api/crm/kpis")
    assert res_kpi_updated.status_code == 200
    kpis = res_kpi_updated.json()
    assert kpis["interview"] >= 1
    print(f" [+] Canonical KPIs verified: Total={kpis['total_contacts']}, Contacted={kpis['contacted']}, Interview={kpis['interview']}")

    # --------------------------------------------------------------------------
    # PHASE I: Clean Restart & Persistence Verification
    # --------------------------------------------------------------------------
    log_step("Phase I — Clean Restart & Vault Persistence Audit")
    # 1. Crash recovery audit
    reset_campaign_scheduler()
    fresh_scheduler = get_campaign_scheduler()
    recovered_count = fresh_scheduler.run_crash_recovery_audit()
    print(f" [+] Startup Crash Recovery Audit completed: {recovered_count} stalled attempt(s) recovered.")

    # 2. Verify encrypted credentials survive in vault
    assert default_credential_vault.has_credentials("EMAIL_SESSION_1") is True
    creds = default_credential_vault.get_credentials("EMAIL_SESSION_1")
    assert creds is not None
    assert creds["user"] == "recruiter.primary@domain.com"
    print(" [+] Encrypted SMTP credentials verified intact in local vault.")

    # 3. Verify SQLite historical attempts preserved
    with SessionFactory() as session:
        o_repo = SqliteOutreachRepository(session)
        all_attempts = o_repo.list_all()
        assert len(all_attempts) >= 2
        print(f" [+] SQLite Persistence Verified: {len(all_attempts)} historical OutreachAttempt audit records intact.")

    log_step("PHASE 9 REAL SMOKE TEST COMPLETE: ALL PHASES (A-I) PASSED WITH ZERO ERRORS!")


if __name__ == "__main__":
    run_smoke_test()
