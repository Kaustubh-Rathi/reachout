import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import outreach as outreach_api
from app.api.dependencies import get_db_session
from app.composition import build_repositories
from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.main import app


@pytest.fixture
def failed_attempt_api(isolated_db):
    engine, session_factory = isolated_db

    def override_get_db_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    with TestClient(app) as client:
        yield client, session_factory
    app.dependency_overrides.pop(get_db_session, None)
    engine.dispose()


def _seed_failed_attempts(session_factory):
    with session_factory() as session:
        repos = build_repositories(session)
        repos.company.save(Company.create(name="=Acme Corp", company_id="cmp_acme"))
        repos.company.save(Company.create(name="Beta Labs", company_id="cmp_beta"))
        contacts = (
            Contact(contact_id="cnt_alice", company_id="cmp_acme", name="Alice", phone="+919876543210"),
            Contact(contact_id="cnt_bob", company_id="cmp_beta", name="Bob", email="bob@example.com"),
            Contact(contact_id="cnt_carol", company_id="cmp_acme", name="Carol", phone="+919876543211"),
            Contact(contact_id="cnt_dave", company_id="cmp_acme", name="Dave", phone="+919876543212"),
        )
        for contact in contacts:
            repos.contact.save(contact)
        repos.sender.save(
            SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="mock",
                identity="+919999999999",
                display_name="WhatsApp Sender",
                sender_id="snd_failed_whatsapp",
            )
        )
        repos.sender.save(
            SenderAccount.create(
                channel=Channel.EMAIL,
                provider="mock",
                identity="sender@example.com",
                display_name="Email Sender",
                sender_id="snd_failed_email",
            )
        )
        repos.campaign.save(
            Campaign.create(
                name="WhatsApp A",
                channel=Channel.WHATSAPP,
                campaign_id="cmp_failed_wa_a",
                sender_account_ids=["snd_failed_whatsapp"],
            )
        )
        repos.campaign.save(
            Campaign.create(
                name="WhatsApp B",
                channel=Channel.WHATSAPP,
                campaign_id="cmp_failed_wa_b",
                sender_account_ids=["snd_failed_whatsapp"],
            )
        )
        repos.campaign.save(
            Campaign.create(
                name="Email A",
                channel=Channel.EMAIL,
                campaign_id="cmp_failed_email",
                sender_account_ids=["snd_failed_email"],
            )
        )
        started_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        attempts = (
            ("cnt_alice", Channel.WHATSAPP, "cmp_failed_wa_a", "ERR_NOT_ON_WHATSAPP", "=SUM(1,2)", 4),
            ("cnt_bob", Channel.EMAIL, "cmp_failed_email", "ERR_TIMEOUT", "Mailbox unavailable", 3),
            ("cnt_carol", Channel.WHATSAPP, "cmp_failed_wa_b", "ERR_TIMEOUT", "Temporary failure", 2),
            ("cnt_dave", Channel.WHATSAPP, "cmp_failed_wa_a", "ERR_TIMEOUT", "Temporary failure", 1),
        )
        for contact_id, channel, campaign_id, failure_code, failure_detail, minute in attempts:
            completed_at = started_at + timedelta(minutes=minute)
            attempt = OutreachAttempt.prepare(
                contact_id=contact_id,
                sender_account_id="snd_failed_email" if channel == Channel.EMAIL else "snd_failed_whatsapp",
                channel=channel,
                attempt_type=AttemptType.AUTOMATIC,
                campaign_id=campaign_id,
                message_body="Hello",
                destination="bob@example.com" if channel == Channel.EMAIL else f"+9198765432{10 + minute}",
                prepared_at=completed_at - timedelta(minutes=1),
                idempotency_key=f"failed-{contact_id}",
            )
            attempt.mark_failed(failure_code, failure_detail, completed_at)
            repos.outreach.save(attempt)
        sent = OutreachAttempt.prepare(
            contact_id="cnt_alice",
            sender_account_id="snd_failed_whatsapp",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            campaign_id="cmp_failed_wa_a",
            message_body="Hello",
            destination="+919876543220",
            idempotency_key="sent-excluded",
        )
        sent.mark_sent("provider-ref", started_at + timedelta(minutes=5))
        repos.outreach.save(sent)
        session.commit()


def test_failed_attempts_shape_filtering_and_pagination(failed_attempt_api):
    client, session_factory = failed_attempt_api
    _seed_failed_attempts(session_factory)

    first_page = client.get("/api/outreach/failures", params={"limit": 1})
    assert first_page.status_code == 200
    payload = first_page.json()
    assert set(payload) == {
        "items",
        "total",
        "count",
        "offset",
        "limit",
        "has_more",
        "failed_destinations",
        "failure_codes",
    }
    assert (payload["total"], payload["count"], payload["offset"], payload["limit"]) == (4, 1, 0, 1)
    assert payload["has_more"] is True
    assert payload["failed_destinations"] == 4
    assert payload["failure_codes"] == ["ERR_NOT_ON_WHATSAPP", "ERR_TIMEOUT"]
    assert set(payload["items"][0]) == {
        "attempt_id",
        "contact_id",
        "contact_name",
        "company_id",
        "company_name",
        "campaign_id",
        "attempt_type",
        "channel",
        "destination",
        "normalized_destination",
        "sender_account_id",
        "template_id",
        "status",
        "failure_code",
        "failure_detail",
        "failure_class",
        "prepared_at",
        "started_at",
        "completed_at",
    }
    assert payload["items"][0]["contact_name"] == "Alice"
    assert payload["items"][0]["company_name"] == "=Acme Corp"
    assert payload["items"][0]["status"] == OutreachStatus.FAILED.value
    assert payload["items"][0]["failure_class"] == "PERMANENT"
    assert payload["items"][0]["completed_at"].startswith("2026-08-17T12:04:00")

    second_page = client.get("/api/outreach/failures", params={"limit": 1, "offset": 1})
    assert second_page.status_code == 200
    assert second_page.json()["items"][0]["contact_name"] == "Bob"
    assert second_page.json()["has_more"] is True

    filtered = client.get(
        "/api/outreach/failures",
        params={"channel": "whatsapp", "failure_code": "ERR_TIMEOUT", "limit": 10},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert filtered.json()["failure_codes"] == ["ERR_NOT_ON_WHATSAPP", "ERR_TIMEOUT"]
    assert [item["contact_name"] for item in filtered.json()["items"]] == ["Carol", "Dave"]

    searched = client.get("/api/outreach/failures", params={"search": "alice"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["contact_name"] == "Alice"


def test_failed_attempt_csv_export_headers_content_and_formula_safety(failed_attempt_api):
    client, session_factory = failed_attempt_api
    _seed_failed_attempts(session_factory)

    response = client.get(
        "/api/outreach/failures/export/csv",
        params={"campaign_id": "cmp_failed_wa_a"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == "attachment; filename=reachout_failed_attempts.csv"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert list(rows[0]) == [
        "Completed At",
        "Contact",
        "Company",
        "Campaign ID",
        "Channel",
        "Destination",
        "Failure Class",
        "Failure Code",
        "Failure Detail",
    ]
    assert len(rows) == 2
    assert rows[0]["Contact"] == "Alice"
    assert rows[0]["Company"] == "'=Acme Corp"
    assert rows[0]["Campaign ID"] == "cmp_failed_wa_a"
    assert rows[0]["Channel"] == "WHATSAPP"
    assert rows[0]["Failure Class"] == "PERMANENT"
    assert rows[0]["Failure Code"] == "ERR_NOT_ON_WHATSAPP"
    assert rows[0]["Failure Detail"] == "'=SUM(1,2)"
    assert rows[1]["Contact"] == "Dave"
    assert rows[1]["Failure Detail"] == "Temporary failure"


@pytest.mark.parametrize(
    "path",
    ["/api/outreach/failures", "/api/outreach/failures/export/csv"],
)
def test_failed_attempt_endpoints_reject_invalid_channel(failed_attempt_api, path):
    client, _ = failed_attempt_api

    response = client.get(path, params={"channel": "sms"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Channel must be WHATSAPP or EMAIL"}


def test_failed_attempt_csv_export_enforces_row_bound(failed_attempt_api, monkeypatch):
    client, session_factory = failed_attempt_api
    _seed_failed_attempts(session_factory)
    monkeypatch.setattr(outreach_api, "FAILED_EXPORT_LIMIT", 2)

    response = client.get("/api/outreach/failures/export/csv")

    assert response.status_code == 413
    assert response.json() == {
        "detail": "Failed-attempt exports are limited to 2 rows. Narrow the filters or archive old attempts."
    }
