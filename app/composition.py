"""Process-wide composition root.

Owns the long-lived adapter singletons (event bus, pacing, sessions, vault) and
the repository bundle factory so infrastructure modules do not construct concrete
adapters directly. Tests inject fakes; production resolves the canonical
instances here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.infrastructure.security.credential_vault import CredentialVault
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


class SyncSummaryStore:
    """Thread-safe holder for the most recent sync summary (separate request)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._summary: Optional[Any] = None

    def set(self, summary: Any) -> None:
        with self._lock:
            self._summary = summary

    def get(self) -> Optional[Any]:
        with self._lock:
            return self._summary


@lru_cache(maxsize=1)
def get_sync_summary_store() -> SyncSummaryStore:
    """Return the process-wide sync summary store."""
    return SyncSummaryStore()


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    """Return the process-wide canonical rate limiter."""
    return RateLimiter()


@lru_cache(maxsize=1)
def get_session_manager() -> WhatsAppSessionManager:
    """Return the process-wide canonical WhatsApp session manager."""
    return WhatsAppSessionManager()


@lru_cache(maxsize=1)
def get_credential_vault() -> CredentialVault:
    """Return the process-wide canonical encrypted credential vault."""
    return CredentialVault()


# ---------------------------------------------------------------------------
# Provider resolution (canonical singletons + test overrides)
# ---------------------------------------------------------------------------
_provider_lock = threading.Lock()
_whatsapp_override: Optional[WhatsAppProvider] = None
_email_override: Optional[EmailProvider] = None
_whatsapp_singleton: Optional[WhatsAppProvider] = None
_email_singleton: Optional[EmailProvider] = None


def set_whatsapp_provider(provider: Optional[WhatsAppProvider]) -> None:
    """Override the WhatsApp provider instance (test seam)."""
    global _whatsapp_override
    _whatsapp_override = provider


def set_email_provider(provider: Optional[EmailProvider]) -> None:
    """Override the Email provider instance (test seam)."""
    global _email_override
    _email_override = provider


def reset_provider_overrides() -> None:
    """Clear runtime provider overrides."""
    global _whatsapp_override, _email_override
    _whatsapp_override = None
    _email_override = None


def get_whatsapp_provider() -> WhatsAppProvider:
    """Resolve the active WhatsApp provider (override, else canonical singleton)."""
    global _whatsapp_singleton
    if _whatsapp_override is not None:
        return _whatsapp_override
    if _whatsapp_singleton is None:
        from app.infrastructure.providers.factory import create_whatsapp_provider

        _whatsapp_singleton = create_whatsapp_provider(session_manager=get_session_manager())
    return _whatsapp_singleton


def get_email_provider() -> EmailProvider:
    """Resolve the active Email provider (override, else canonical singleton)."""
    global _email_singleton
    if _email_override is not None:
        return _email_override
    if _email_singleton is None:
        from app.infrastructure.providers.factory import create_email_provider

        _email_singleton = create_email_provider(credential_vault=get_credential_vault())
    return _email_singleton
