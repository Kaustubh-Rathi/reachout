"""Injectable service dependency bundle (hexagonal composition helper).

Application services depend on the port-typed collaborators gathered in
`ServiceContext` instead of constructing concrete SQLite repositories or reaching
for the global event bus. `build_service_context` is the single composition root
that wires the concrete adapters, keeping the services themselves free to be
driven by fakes in tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ports.infrastructure import Clock, EventPublisher, SystemClock
from app.ports.repositories import (
    CampaignRepository,
    CompanyRepository,
    ContactRepository,
    OutreachRepository,
    ReminderRepository,
    SenderRepository,
    SuppressionRepository,
    TemplateRepository,
)


@dataclass
class ServiceContext:
    """Port-typed collaborators shared by application services."""

    contact_repo: ContactRepository
    company_repo: CompanyRepository
    campaign_repo: CampaignRepository
    outreach_repo: OutreachRepository
    sender_repo: SenderRepository
    template_repo: TemplateRepository
    reminder_repo: ReminderRepository
    suppression_repo: SuppressionRepository
    event_publisher: EventPublisher
    clock: Clock


def build_service_context(session: Session) -> ServiceContext:
    """Composition root: wire the concrete SQLite adapters and the event bus."""
    from app.infrastructure.events.event_bus import default_event_bus
    from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
    from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
    from app.infrastructure.repositories.sqlite_reminder_repository import SqliteReminderRepository
    from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
    from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
    from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository

    return ServiceContext(
        contact_repo=SqliteContactRepository(session),
        company_repo=SqliteCompanyRepository(session),
        campaign_repo=SqliteCampaignRepository(session),
        outreach_repo=SqliteOutreachRepository(session),
        sender_repo=SqliteSenderRepository(session),
        template_repo=SqliteTemplateRepository(session),
        reminder_repo=SqliteReminderRepository(session),
        suppression_repo=SqliteSuppressionRepository(session),
        event_publisher=default_event_bus,
        clock=SystemClock(),
    )
