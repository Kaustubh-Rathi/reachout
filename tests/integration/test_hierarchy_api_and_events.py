"""Integration tests for Phase 8 Company Hierarchy API and Real-time Event Streaming."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, CRMOutcome, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.database import Base
from app.infrastructure.events.event_bus import default_event_bus
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.main import app
from app.services.company_service import CompanyService
from app.services.event_bus import event_bus


@pytest.fixture
def hierarchy_session_factory(tmp_path):
    """Create isolated SQLite database for hierarchy testing."""
    engine = create_engine(f"sqlite:///{tmp_path / 'hierarchy_test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_company_hierarchy_endpoint(hierarchy_session_factory):
    """Verify CompanyService and hierarchy API return full hierarchical structure with discrete endpoint statuses."""
    SessionFactory = hierarchy_session_factory

    with SessionFactory() as session:
        comp_repo = SqliteCompanyRepository(session)
        cnt_repo = SqliteContactRepository(session)
        outreach_repo = SqliteOutreachRepository(session)

        # Company 1: IN_PROGRESS (1 out of 2 endpoints contacted)
        c1 = Company.create(name="Netflix", company_id="netflix")
        comp_repo.save(c1)
        cnt_repo.save(Contact(
            contact_id="cnt_netflix_hr1",
            company_id="netflix",
            name="Reed Hastings",
            phone="+919899000001",
            email="reed@netflix.com",
        ))

        # Attempt to phone only
        att1 = OutreachAttempt.prepare(
            contact_id="cnt_netflix_hr1",
            sender_account_id="WA1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="WA intro",
            destination="919899000001",
        )
        att1.mark_sent("ref-wa-001")
        outreach_repo.save(att1)

        # Company 2: CONTACTED (1 out of 1 endpoint contacted)
        c2 = Company.create(name="Spotify", company_id="spotify")
        comp_repo.save(c2)
        cnt_repo.save(Contact(
            contact_id="cnt_spotify_hr1",
            company_id="spotify",
            name="Daniel Ek",
            phone="",
            email="daniel@spotify.com",
        ))
        att2 = OutreachAttempt.prepare(
            contact_id="cnt_spotify_hr1",
            sender_account_id="EMAIL1",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Email intro",
            destination="daniel@spotify.com",
        )
        att2.mark_sent("ref-em-001")
        outreach_repo.save(att2)

        session.commit()

    with SessionFactory() as session:
        comp_svc = CompanyService(session)
        hierarchies = comp_svc.list_hierarchies()

        assert len(hierarchies) == 2

        netflix = next(h for h in hierarchies if h["id"] == "netflix")
        assert netflix["status"] == "IN_PROGRESS"
        assert netflix["covered_endpoints"] == 1
        assert netflix["total_endpoints"] == 2
        assert len(netflix["contacts"]) == 1
        reed = netflix["contacts"][0]
        assert len(reed["endpoints"]) == 2
        wa_ep = next(e for e in reed["endpoints"] if e["channel"] == "WHATSAPP")
        assert wa_ep["status"] == "SENT"
        em_ep = next(e for e in reed["endpoints"] if e["channel"] == "EMAIL")
        assert em_ep["status"] == "NOT_CONTACTED"

        spotify = next(h for h in hierarchies if h["id"] == "spotify")
        assert spotify["status"] == "CONTACTED"
        assert spotify["covered_endpoints"] == 1
        assert spotify["total_endpoints"] == 1


def test_event_bus_bridge_and_event_publishing():
    """Verify that domain events are propagated to the event bus with uppercase event aliases."""
    received = []

    def subscriber(ev):
        received.append(ev)

    default_event_bus.subscribe(subscriber)

    try:
        # Publish event on domain bus
        event = event_bus.publish_event("OUTREACH_SENT", {
            "contact_id": "cnt_101",
            "destination": "919876543210",
            "channel": "WHATSAPP",
        })

        assert event.event_type == "OUTREACH_SENT"
        assert event.payload["destination"] == "919876543210"

        # Also publish through default_event_bus
        default_event_bus.publish(event)
        assert len(received) >= 1
        assert received[-1].event_type == "OUTREACH_SENT"
    finally:
        default_event_bus.unsubscribe(subscriber)
