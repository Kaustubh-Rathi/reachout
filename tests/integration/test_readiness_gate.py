"""Integration tests for the outreach readiness gate and session authentication APIs."""

import pytest
from fastapi.testclient import TestClient

from app.domain.campaign import Campaign
from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.main import app
from app.services.sender_service import SenderService


@pytest.fixture
def client():
    return TestClient(app)


def test_readiness_gate_blocks_when_no_active_whatsapp(client):
    """Verify Start Outreach is blocked with structured error when no WhatsApp sessions are active."""
    from app.domain.company import Company
    from app.domain.contact import Contact
    from app.domain.message_template import MessageTemplate
    from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository

    with SessionFactory() as session:
        sender_svc = SenderService(session)
        comp_repo = SqliteCompanyRepository(session)
        cnt_repo = SqliteContactRepository(session)
        tpl_repo = SqliteTemplateRepository(session)

        # Seed company, contact and template so readiness proceeds to sender check
        if not comp_repo.list_all():
            comp_repo.save(Company.create(name="Gate Company", domain="gate.com", company_id="cmp_gate_test"))
        if not cnt_repo.list_all():
            cnt_repo.save(
                Contact(
                    contact_id="cnt_gate_test",
                    company_id="cmp_gate_test",
                    name="Gate Test",
                    phone="919999900000",
                    email="gate@example.com",
                )
            )
        if not tpl_repo.list_by_channel(Channel.WHATSAPP):
            tpl_repo.save(MessageTemplate.create(name="WA Tpl", channel=Channel.WHATSAPP, body="Hello"))

        # Ensure all WA senders are AUTH_REQUIRED
        for sender_channel in (Channel.WHATSAPP, Channel.EMAIL):
            for sender in sender_svc.repo.list_by_channel(sender_channel):
                sender.status = SenderStatus.AUTH_REQUIRED
                sender_svc.repo.save(sender)
        session.commit()

    # 1. Test GET /api/campaigns/readiness pre-flight
    res = client.get("/api/campaigns/readiness?channel=WHATSAPP")
    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is False
    assert data["reason"] == "NO_ACTIVE_WHATSAPP_SESSION"

    # 2. Test POST /api/campaigns/quick-start blocks with 400 and structured error
    res_start = client.post("/api/campaigns/quick-start", json={"channel": "WHATSAPP", "max_count": 5})
    assert res_start.status_code == 400
    err_body = res_start.json()
    assert err_body["detail"]["error"] == "OUTREACH_NOT_READY"
    assert err_body["detail"]["reason"] == "NO_ACTIVE_WHATSAPP_SESSION"


def test_readiness_uses_active_fallback_channel(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.composition import build_repositories
    from app.domain.company import Company
    from app.domain.contact import Contact
    from app.domain.message_template import MessageTemplate
    from app.domain.sender_account import SenderAccount
    from app.infrastructure.database import Base
    from app.services.campaign_service import CampaignService

    engine = create_engine(f"sqlite:///{tmp_path / 'fallback_readiness.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        repos = build_repositories(session)
        repos.company.save(Company.create(name="Fallback Co", company_id="fallback_co"))
        repos.contact.save(
            Contact(
                contact_id="fallback_contact",
                company_id="fallback_co",
                name="Fallback Contact",
                phone="+919999900001",
                email="fallback@example.com",
            )
        )
        repos.template.save(
            MessageTemplate.create(template_id="fallback_email", name="Fallback", channel=Channel.EMAIL, body="Hello")
        )
        email_sender = SenderAccount.create(
            sender_id="fallback_email_sender",
            channel=Channel.EMAIL,
            provider="smtp",
            identity="fallback@example.com",
            display_name="Fallback Email",
        )
        email_sender.mark_status(SenderStatus.ACTIVE)
        repos.sender.save(email_sender)
        session.commit()

        readiness = CampaignService(session).validate_outreach_readiness(Channel.WHATSAPP)

    assert readiness["ready"] is True
    assert readiness["reason"] is None


def test_readiness_honors_campaign_resource_restrictions(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.composition import build_repositories
    from app.domain.company import Company
    from app.domain.contact import Contact
    from app.domain.message_template import MessageTemplate
    from app.infrastructure.database import Base
    from app.services.campaign_service import CampaignService

    engine = create_engine(f"sqlite:///{tmp_path / 'restricted_readiness.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        repos = build_repositories(session)
        repos.company.save(Company.create(name="Restricted Co", company_id="restricted_co"))
        repos.contact.save(
            Contact(
                contact_id="restricted_contact",
                company_id="restricted_co",
                name="Restricted Contact",
                phone="+919999900002",
            )
        )
        repos.template.save(
            MessageTemplate.create(
                template_id="restricted_template",
                name="Restricted Template",
                channel=Channel.WHATSAPP,
                body="Hello",
            )
        )
        restricted_sender = SenderAccount.create(
            sender_id="restricted_sender",
            channel=Channel.WHATSAPP,
            provider="mock",
            identity="+919999900000",
            display_name="Restricted Sender",
        )
        restricted_sender.mark_status(SenderStatus.INACTIVE)
        repos.sender.save(restricted_sender)
        active_sender = SenderAccount.create(
            sender_id="active_sender",
            channel=Channel.WHATSAPP,
            provider="mock",
            identity="+919999900001",
            display_name="Active Sender",
        )
        repos.sender.save(active_sender)
        campaign = Campaign.create(
            name="Restricted Campaign",
            channel=Channel.WHATSAPP,
            campaign_id="restricted_campaign",
            template_ids=["restricted_template"],
            sender_account_ids=["restricted_sender"],
        )
        repos.campaign.save(campaign)
        session.commit()

        unavailable = CampaignService(session).validate_outreach_readiness(
            Channel.WHATSAPP,
            campaign_id=campaign.id,
        )
        assert unavailable["ready"] is False
        assert unavailable["reason"] == "NO_ACTIVE_WHATSAPP_SESSION"

        campaign.sender_account_ids = ["active_sender"]
        campaign.template_ids = ["missing_template"]
        repos.campaign.save(campaign)
        session.commit()

        missing = CampaignService(session).validate_outreach_readiness(
            Channel.WHATSAPP,
            campaign_id=campaign.id,
        )
        assert missing["ready"] is False
        assert missing["reason"] == "MISSING_TEMPLATES"

        campaign.template_ids = ["restricted_template"]
        repos.campaign.save(campaign)
        session.commit()

        ready = CampaignService(session).validate_outreach_readiness(
            Channel.WHATSAPP,
            campaign_id=campaign.id,
        )
        assert ready["ready"] is True


def test_whatsapp_auth_flow_activates_sender_and_unblocks_readiness(client):
    """Verify starting auth and updating sender status to ACTIVE unblocks readiness."""
    # 1. Configure WhatsApp sessions
    res_cfg = client.post("/api/senders/whatsapp/configure", json={"count": 2})
    assert res_cfg.status_code == 200

    # 2. Start authentication for WA_SESSION_1
    res_start_auth = client.post("/api/senders/whatsapp/WA_SESSION_1/auth/start")
    assert res_start_auth.status_code == 200

    # 3. Check status
    res_status = client.get("/api/senders/whatsapp/WA_SESSION_1/auth/status")
    assert res_status.status_code == 200
    assert res_status.json()["sender_id"] == "WA_SESSION_1"

    # 4. Update status to ACTIVE
    res_status_update = client.put("/api/senders/WA_SESSION_1/status", json={"status": "ACTIVE"})
    assert res_status_update.status_code == 200
    assert res_status_update.json()["status"] == "ACTIVE"

    # 5. Check readiness now passes
    res_readiness = client.get("/api/senders/readiness")
    assert res_readiness.status_code == 200
    assert res_readiness.json()["whatsapp"]["ready"] is True


def test_email_configuration_and_verification_flow(client):
    """Verify configuring email credentials and verifying connection works."""
    payload = {
        "id": "EMAIL_SESSION_TEST",
        "identity": "test.sender@domain.com",
        "display_name": "Test Email Session",
        "host": "smtp.test.com",
        "port": 587,
        "user": "test.sender@domain.com",
        "password": "valid_mock_password",
        "verify_now": True,
    }
    res = client.post("/api/senders/email/configure", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["verified"] is True
    assert data["status"] == "ACTIVE"

    # Test verify endpoint
    res_verify = client.post("/api/senders/email/EMAIL_SESSION_TEST/verify")
    assert res_verify.status_code == 200
    assert res_verify.json()["verified"] is True
