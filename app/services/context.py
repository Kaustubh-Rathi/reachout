"""Injectable service dependency bundle (hexagonal composition helper).

Application services depend on the port-typed collaborators gathered in
`ServiceContext` instead of constructing concrete SQLite repositories or reaching
for the global event bus. `build_service_context` is the single composition root
that wires the concrete adapters, keeping the services themselves free to be
driven by fakes in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.ports.infrastructure import (
    CampaignScheduler,
    Clock,
    CredentialVault,
    EventPublisher,
    RateLimiter,
    SessionManager,
    SystemClock,
)
from app.ports.providers import EmailProvider, WhatsAppProvider
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
from app.ports.source import SourceSynchronizer


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
    whatsapp_provider: WhatsAppProvider
    email_provider: EmailProvider
    rate_limiter: RateLimiter
    session_manager: SessionManager
    credential_vault: CredentialVault
    source_synchronizer: SourceSynchronizer
    scheduler: CampaignScheduler
    # Sessionmaker used by background callbacks that cannot share the request
    # session. Typed loosely to avoid leaking SQLAlchemy types into ports.
    session_factory: Any = None


def build_service_context(
    session: Session,
    *,
    event_publisher: Optional[EventPublisher] = None,
    clock: Optional[Clock] = None,
    session_factory: Any = None,
) -> ServiceContext:
    """Composition root: wire the concrete adapters and the process-wide singletons.

    All concrete infrastructure imports live here (and only here) in the
    application layer, so services remain free of adapter dependencies.
    """
    from app.infrastructure.database import SessionFactory
    from app.infrastructure.events.event_bus import default_event_bus
    from app.infrastructure.providers.factory import get_email_provider, get_whatsapp_provider
    from app.infrastructure.providers.session_manager import default_session_manager
    from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
    from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
    from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
    from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
    from app.infrastructure.repositories.sqlite_reminder_repository import SqliteReminderRepository
    from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
    from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
    from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
    from app.infrastructure.scheduler.campaign_scheduler import get_campaign_scheduler
    from app.infrastructure.scheduler.rate_limiter import default_rate_limiter
    from app.infrastructure.security.credential_vault import default_credential_vault
    from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer

    return ServiceContext(
        contact_repo=SqliteContactRepository(session),
        company_repo=SqliteCompanyRepository(session),
        campaign_repo=SqliteCampaignRepository(session),
        outreach_repo=SqliteOutreachRepository(session),
        sender_repo=SqliteSenderRepository(session),
        template_repo=SqliteTemplateRepository(session),
        reminder_repo=SqliteReminderRepository(session),
        suppression_repo=SqliteSuppressionRepository(session),
        event_publisher=event_publisher or default_event_bus,
        clock=clock or SystemClock(),
        whatsapp_provider=get_whatsapp_provider(),
        email_provider=get_email_provider(),
        rate_limiter=default_rate_limiter,
        session_manager=default_session_manager,
        credential_vault=default_credential_vault,
        source_synchronizer=DatabaseSourceSynchronizer(session),
        scheduler=get_campaign_scheduler(),
        session_factory=session_factory or SessionFactory,
    )
