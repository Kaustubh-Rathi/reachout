"""Integration tests for sender-session guards.

Verifies that non-ACTIVE senders can never be used to dispatch, that a provider
authentication failure downgrades the sender, and that the worker enforces the
same invariant as the manual-send service.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.composition import build_repositories
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel, OutreachStatus, SenderStatus
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import SystemClock
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'session_guards.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        SqliteCompanyRepository(session).save(Company.create(name="Acme", company_id="acme"))
        SqliteContactRepository(session).save(
            Contact(contact_id="cnt_acme_1", company_id="acme", name="Ada Lovelace", phone="919900000001")
        )
        SqliteTemplateRepository(session).save(
            MessageTemplate.create(
                template_id="tmpl_guard",
                name="Guard Template",
                channel=Channel.WHATSAPP,
                body="Hello {first_name}",
            )
        )
        sender = SenderAccount.create(
            sender_id="WA-GUARD",
            channel=Channel.WHATSAPP,
            provider="mock",
            identity="+919900000000",
            display_name="Guard Sender",
        )
        sender.mark_status(SenderStatus.INACTIVE)
        SqliteSenderRepository(session).save(sender)
        session.commit()
    return factory


def _load_sender(factory, sender_id="WA-GUARD"):
    with factory() as session:
        return SqliteSenderRepository(session).get_by_id(sender_id)


def test_manual_send_rejects_non_active_sender(session_factory):
    """OutreachService must refuse to dispatch from a non-ACTIVE sender."""
    with session_factory() as session:
        svc = OutreachService(session, whatsapp_provider=FakeWhatsAppProvider(), email_provider=FakeEmailProvider())
        with pytest.raises(ValueError, match="SENDER_NOT_ACTIVE"):
            svc.send_whatsapp(contact_id="cnt_acme_1", sender_id="WA-GUARD")


def test_worker_refuses_non_active_sender(session_factory):
    """OutreachWorker.execute_attempt must record a FAILED attempt, never dispatch."""
    sender = _load_sender(session_factory)
    provider = FakeWhatsAppProvider()
    worker = OutreachWorker(
        session_factory=session_factory,
        whatsapp_provider=provider,
        email_provider=FakeEmailProvider(),
        rate_limiter=RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01}),
        event_publisher=EventBus(),
        repository_factory=build_repositories,
        clock=SystemClock(),
    )
    with session_factory() as session:
        template = SqliteTemplateRepository(session).get_by_id("tmpl_guard")

    attempt = worker.execute_attempt(contact_id="cnt_acme_1", sender_account=sender, template=template)

    assert attempt.status == OutreachStatus.FAILED
    assert attempt.failure_code == "ERR_SENDER_NOT_ACTIVE"
    assert provider.sent_calls == []


def test_provider_auth_failure_downgrades_sender(session_factory):
    """A provider ERR_AUTH_REQUIRED must flip the sender to AUTH_REQUIRED."""
    with session_factory() as session:
        sender = SqliteSenderRepository(session).get_by_id("WA-GUARD")
        sender.status = SenderStatus.ACTIVE
        SqliteSenderRepository(session).save(sender)
        session.commit()

    provider = FakeWhatsAppProvider()
    provider.fail_next_with = ("ERR_AUTH_REQUIRED", "WhatsApp Web requires QR scan")

    with session_factory() as session:
        svc = OutreachService(session, whatsapp_provider=provider, email_provider=FakeEmailProvider())
        result = svc.send_whatsapp(contact_id="cnt_acme_1", sender_id="WA-GUARD")
        assert result["success"] is False

    assert _load_sender(session_factory).status == SenderStatus.AUTH_REQUIRED


def test_startup_reconcile_downgrades_active_sender_without_profile(session_factory, tmp_path):
    """An ACTIVE WhatsApp sender with no on-disk profile must be downgraded on startup."""
    from app.infrastructure.providers.session_manager import WhatsAppSessionManager
    from app.services.sender_service import SenderService

    with session_factory() as session:
        sender = SqliteSenderRepository(session).get_by_id("WA-GUARD")
        sender.status = SenderStatus.ACTIVE
        SqliteSenderRepository(session).save(sender)
        session.commit()

    with session_factory() as session:
        manager = WhatsAppSessionManager(sessions_root=tmp_path / "sessions")
        SenderService(session, session_manager=manager).reconcile_sender_states()

    assert _load_sender(session_factory).status == SenderStatus.AUTH_REQUIRED


class StubHealthSessionManager:
    """Session manager double with a scripted live-probe result."""

    def __init__(self, probe_result):
        self._probe_result = probe_result

    def check_session_status(self, sender_id, timeout_seconds=20):
        return self._probe_result


def _activate_guard_sender(session_factory):
    with session_factory() as session:
        sender = SqliteSenderRepository(session).get_by_id("WA-GUARD")
        sender.status = SenderStatus.ACTIVE
        SqliteSenderRepository(session).save(sender)
        session.commit()


def test_health_check_keeps_synced_sender_active(session_factory):
    """A live probe reporting ACTIVE must leave the sender ACTIVE."""
    from app.services.sender_service import SenderService

    _activate_guard_sender(session_factory)
    with session_factory() as session:
        result = SenderService(
            session, session_manager=StubHealthSessionManager(SenderStatus.ACTIVE)
        ).check_whatsapp_session_health("WA-GUARD")

    assert result["probe"] == "ACTIVE"
    assert result["status"] == "ACTIVE"
    assert result["status_changed"] is False
    assert _load_sender(session_factory).status == SenderStatus.ACTIVE


def test_health_check_downgrades_dead_login(session_factory):
    """Positive evidence of a dead login (QR/AUTH_REQUIRED) must downgrade the sender."""
    from app.services.sender_service import SenderService

    for probe, expected in (
        (SenderStatus.QR_REQUIRED, SenderStatus.QR_REQUIRED),
        (SenderStatus.AUTH_REQUIRED, SenderStatus.AUTH_REQUIRED),
    ):
        _activate_guard_sender(session_factory)
        with session_factory() as session:
            result = SenderService(
                session, session_manager=StubHealthSessionManager(probe)
            ).check_whatsapp_session_health("WA-GUARD")

        assert result["probe"] == expected.value
        assert result["status"] == expected.value
        assert result["status_changed"] is True
        assert _load_sender(session_factory).status == expected


def test_health_check_leaves_sender_untouched_on_inconclusive_probe(session_factory):
    """Errors/disconnects are reported but must not mutate stored status."""
    from app.services.sender_service import SenderService

    _activate_guard_sender(session_factory)
    with session_factory() as session:
        result = SenderService(
            session, session_manager=StubHealthSessionManager(SenderStatus.DISCONNECTED)
        ).check_whatsapp_session_health("WA-GUARD")

    assert result["probe"] == "DISCONNECTED"
    assert result["status"] == "ACTIVE"
    assert result["status_changed"] is False
    assert _load_sender(session_factory).status == SenderStatus.ACTIVE


def test_health_check_rejects_unknown_sender(session_factory):
    """Health checks for unknown ids must raise, mapped to HTTP 404 by the API."""
    from app.services.sender_service import SenderService

    with session_factory() as session:
        with pytest.raises(ValueError, match="not found"):
            SenderService(
                session, session_manager=StubHealthSessionManager(SenderStatus.ACTIVE)
            ).check_whatsapp_session_health("WA-NOPE")
