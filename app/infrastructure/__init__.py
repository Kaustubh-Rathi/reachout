"""Reachout Infrastructure Layer.

Provides SQLite relational persistence, Alembic migrations, source readers/synchronizers,
Playwright & SMTP provider adapters, multi-sender session management, and persistent scheduler.
"""

from app.infrastructure.database import (
    Base,
    SessionFactory,
    create_db_engine,
    engine,
    get_session,
    init_db,
)
from app.infrastructure.events import EventBus, default_event_bus
from app.infrastructure.models import (
    CampaignModel,
    CompanyModel,
    ContactModel,
    CRMEventModel,
    FollowUpReminderModel,
    MessageTemplateModel,
    OutreachAttemptModel,
    SenderAccountModel,
    SourceRecordModel,
    SuppressionRecordModel,
)
from app.infrastructure.providers import (
    PlaywrightWhatsAppProvider,
    SmtpEmailProvider,
    WhatsAppSessionManager,
)
from app.infrastructure.repositories import (
    SqliteCampaignRepository,
    SqliteCompanyRepository,
    SqliteContactRepository,
    SqliteOutreachRepository,
    SqliteReminderRepository,
    SqliteSenderRepository,
    SqliteSuppressionRepository,
    SqliteTemplateRepository,
)
from app.infrastructure.scheduler import (
    OutreachWorker,
    PersistentCampaignScheduler,
    RateLimiter,
)
from app.infrastructure.source import (
    DatabaseSourceSynchronizer,
    TabularSourceReader,
    extract_emails,
    extract_phone_numbers,
    normalize_phone_number,
)

__all__ = [
    # Database
    "Base",
    "engine",
    "create_db_engine",
    "SessionFactory",
    "get_session",
    "init_db",
    # Models
    "CompanyModel",
    "ContactModel",
    "SourceRecordModel",
    "SenderAccountModel",
    "CampaignModel",
    "MessageTemplateModel",
    "OutreachAttemptModel",
    "FollowUpReminderModel",
    "CRMEventModel",
    "SuppressionRecordModel",
    # Repositories
    "SqliteContactRepository",
    "SqliteCompanyRepository",
    "SqliteCampaignRepository",
    "SqliteOutreachRepository",
    "SqliteSenderRepository",
    "SqliteTemplateRepository",
    "SqliteReminderRepository",
    "SqliteSuppressionRepository",
    # Source
    "TabularSourceReader",
    "DatabaseSourceSynchronizer",
    "normalize_phone_number",
    "extract_phone_numbers",
    "extract_emails",
    # Providers
    "WhatsAppSessionManager",
    "PlaywrightWhatsAppProvider",
    "SmtpEmailProvider",
    # Scheduler & Worker
    "RateLimiter",
    "OutreachWorker",
    "PersistentCampaignScheduler",
    # Events
    "EventBus",
    "default_event_bus",
]
