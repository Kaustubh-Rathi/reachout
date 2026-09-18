"""Process-wide composition root.

Owns the long-lived adapter singletons (event bus, pacing, sessions, vault) and
the repository bundle factory so infrastructure modules do not construct concrete
adapters directly. Tests inject fakes; production resolves the canonical
instances here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.events.event_bus import EventBus
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
class Repositories:
    """Bundle of port-typed repositories sharing one session."""

    contact: ContactRepository
    company: CompanyRepository
    campaign: CampaignRepository
    outreach: OutreachRepository
    sender: SenderRepository
    template: TemplateRepository
    reminder: ReminderRepository
    suppression: SuppressionRepository


def build_repositories(session: Session) -> Repositories:
    """Wire the concrete SQLite repository adapters for a session."""
    from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
    from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
    from app.infrastructure.repositories.sqlite_reminder_repository import SqliteReminderRepository
    from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
    from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
    from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository

    return Repositories(
        contact=SqliteContactRepository(session),
        company=SqliteCompanyRepository(session),
        campaign=SqliteCampaignRepository(session),
        outreach=SqliteOutreachRepository(session),
        sender=SqliteSenderRepository(session),
        template=SqliteTemplateRepository(session),
        reminder=SqliteReminderRepository(session),
        suppression=SqliteSuppressionRepository(session),
    )


@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    """Return the process-wide canonical event bus."""
    return EventBus()


def get_repository_factory() -> Any:
    """Return the canonical repository-bundle factory."""
    return build_repositories
