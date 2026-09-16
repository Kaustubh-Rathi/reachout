"""API-level tests that manual send/resend endpoints persist outreach attempts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.main import app


def _seed(contact_id: str, sender_id: str, template_id: str, channel: Channel, destination: str, identity: str) -> None:
    with SessionFactory() as session:
        SqliteCompanyRepository(session).save(Company.create(name=f"Co {contact_id}", company_id=f"co_{contact_id}"))
        SqliteContactRepository(session).save(
            Contact(
                contact_id=contact_id,
                company_id=f"co_{contact_id}",
                name="API Target",
                phone=destination if channel == Channel.WHATSAPP else None,
                email=destination if channel == Channel.EMAIL else None,
            )
        )
        SqliteSenderRepository(session).save(
            SenderAccount.create(
                sender_id=sender_id,
                channel=channel,
                provider="mock",
                identity=identity,
                display_name=f"Sender {sender_id}",
            )
        )
        SqliteTemplateRepository(session).save(
            MessageTemplate.create(
                template_id=template_id,
                name="API Template",
                channel=channel,
                body="Hello {first_name}",
            )
        )
        session.commit()


def test_api_send_whatsapp_persists_manual_attempt():
    contact_id, sender_id, template_id = "cnt_api_wa", "WA-API", "tmpl_api_wa"
    _seed(contact_id, sender_id, template_id, Channel.WHATSAPP, "919900001111", "+919900001111")
    client = TestClient(app)

    res = client.post(
        "/api/outreach/send-whatsapp",
        json={
            "contact_id": contact_id,
            "sender_id": sender_id,
            "template_id": template_id,
            "destination": "919900001111",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["status"] == "SENT"

    with SessionFactory() as session:
        attempts = SqliteOutreachRepository(session).list_by_contact(contact_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_type == AttemptType.MANUAL
    assert attempts[0].status == OutreachStatus.SENT
    assert attempts[0].destination == "919900001111"


def test_api_send_email_persists_manual_attempt():
    contact_id, sender_id, template_id = "cnt_api_em", "EM-API", "tmpl_api_em"
    _seed(contact_id, sender_id, template_id, Channel.EMAIL, "api.target@example.com", "api.sender@example.com")
    client = TestClient(app)

    res = client.post(
        "/api/outreach/send-email",
        json={
            "contact_id": contact_id,
            "sender_id": sender_id,
            "template_id": template_id,
            "destination": "api.target@example.com",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True

    with SessionFactory() as session:
        attempts = SqliteOutreachRepository(session).list_by_contact(contact_id)
    assert len(attempts) == 1
    assert attempts[0].channel == Channel.EMAIL
    assert attempts[0].attempt_type == AttemptType.MANUAL
    assert attempts[0].status == OutreachStatus.SENT


def test_api_send_rejects_non_active_sender():
    contact_id, sender_id, template_id = "cnt_api_inactive", "WA-INACTIVE", "tmpl_api_inactive"
    _seed(contact_id, sender_id, template_id, Channel.WHATSAPP, "919900002222", "+919900002222")
    from app.domain.enums import SenderStatus

    with SessionFactory() as session:
        sender = SqliteSenderRepository(session).get_by_id(sender_id)
        sender.mark_status(SenderStatus.INACTIVE)
        SqliteSenderRepository(session).save(sender)
        session.commit()

    client = TestClient(app)
    res = client.post(
        "/api/outreach/send-whatsapp",
        json={"contact_id": contact_id, "sender_id": sender_id, "template_id": template_id},
    )
    assert res.status_code == 400
    assert "SENDER_NOT_ACTIVE" in res.json()["detail"]
