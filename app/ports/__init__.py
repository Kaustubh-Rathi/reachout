"""Reachout Architecture Ports.

Defines all inbound and outbound boundary interfaces (Protocols) for repositories,
messaging providers, source readers/synchronizers, and infrastructure utilities.
"""

from app.ports.infrastructure import (
    CampaignScheduler,
    Clock,
    DomainEvent,
    EventPublisher,
    FrozenClock,
    SystemClock,
)
from app.ports.providers import (
    EmailProvider,
    ProviderSendResult,
    ProviderStatusResult,
    WhatsAppProvider,
)
from app.ports.repositories import (
    CampaignRepository,
    CompanyRepository,
    ContactRepository,
    OutreachRepository,
    ReminderRepository,
    SenderRepository,
    TemplateRepository,
)
from app.ports.source import (
    SourceReader,
    SourceRow,
    SourceSynchronizer,
    SyncSummary,
)

__all__ = [
    # Repositories
    "ContactRepository",
    "CompanyRepository",
    "CampaignRepository",
    "OutreachRepository",
    "SenderRepository",
    "TemplateRepository",
    "ReminderRepository",
    # Providers
    "WhatsAppProvider",
    "EmailProvider",
    "ProviderSendResult",
    "ProviderStatusResult",
    # Source
    "SourceReader",
    "SourceSynchronizer",
    "SourceRow",
    "SyncSummary",
    # Infrastructure
    "Clock",
    "SystemClock",
    "FrozenClock",
    "CampaignScheduler",
    "DomainEvent",
    "EventPublisher",
]
