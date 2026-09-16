"""Verify application services use the injected Clock port for timestamps."""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.company import Company
from app.domain.contact import Contact
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.ports.infrastructure import FrozenClock
from app.services.context import build_service_context
from app.services.crm_service import CrmService


def test_crm_service_uses_injected_clock(db_session):
    SqliteCompanyRepository(db_session).save(Company.create(name="Acme", company_id="acme"))
    SqliteContactRepository(db_session).save(
        Contact(contact_id="cnt_clock", company_id="acme", name="Ada Lovelace", phone="919900000001")
    )
    db_session.commit()

    frozen_time = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    context = build_service_context(db_session)
    context.clock = FrozenClock(frozen_time)

    CrmService(db_session, context=context).mark_interested("cnt_clock")

    contact = SqliteContactRepository(db_session).get_by_id("cnt_clock")
    # SQLite stores naive datetimes, so compare wall-clock values.
    assert contact.interested_at.replace(tzinfo=timezone.utc) == frozen_time
    assert contact.updated_at.replace(tzinfo=timezone.utc) == frozen_time
