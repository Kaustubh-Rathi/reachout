"""Provider factory and configuration resolver.

Provides production resolution and test injection for outbound messaging providers.
Production canonical providers:
  WhatsApp -> PlaywrightWhatsAppProvider
  Email    -> SmtpEmailProvider
"""

from __future__ import annotations

from typing import Dict, Optional

from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import (
    WhatsAppSessionManager,
    default_session_manager,
)
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.ports.providers import EmailProvider, WhatsAppProvider

# Module-level test dependency injection overrides
_whatsapp_provider_override: Optional[WhatsAppProvider] = None
_email_provider_override: Optional[EmailProvider] = None

# Canonical production singletons (B2/B3) — created once, reused across requests.
_whatsapp_singleton: Optional[WhatsAppProvider] = None
_email_singleton: Optional[EmailProvider] = None


def set_whatsapp_provider(provider: Optional[WhatsAppProvider]) -> None:
    """Override WhatsApp provider instance for dependency injection in tests."""
    global _whatsapp_provider_override
    _whatsapp_provider_override = provider


def set_email_provider(provider: Optional[EmailProvider]) -> None:
    """Override Email provider instance for dependency injection in tests."""
    global _email_provider_override
    _email_provider_override = provider


def reset_provider_overrides() -> None:
    """Clear all runtime overrides."""
    global _whatsapp_provider_override, _email_provider_override
    _whatsapp_provider_override = None
    _email_provider_override = None


def create_whatsapp_provider(
    session_manager: Optional[WhatsAppSessionManager] = None,
    headless: bool = False,
    timeout_seconds: int = 60,
) -> WhatsAppProvider:
    """Factory creating canonical production WhatsApp provider."""
    return PlaywrightWhatsAppProvider(
        session_manager=session_manager or default_session_manager,
        headless=headless,
        timeout_seconds=timeout_seconds,
    )


def create_email_provider(
    credential_store: Optional[Dict[str, Dict[str, str]]] = None,
    test_redirect_to: Optional[str] = None,
) -> EmailProvider:
    """Factory creating canonical production Email provider."""
    return SmtpEmailProvider(
        credential_store=credential_store,
        test_redirect_to=test_redirect_to,
    )


def get_whatsapp_provider() -> WhatsAppProvider:
    """Resolve active WhatsApp provider (cached singleton) respecting DI overrides."""
    global _whatsapp_singleton
    if _whatsapp_provider_override is not None:
        return _whatsapp_provider_override
    if _whatsapp_singleton is None:
        _whatsapp_singleton = create_whatsapp_provider()
    return _whatsapp_singleton


def get_email_provider() -> EmailProvider:
    """Resolve active Email provider (cached singleton) respecting DI overrides."""
    global _email_singleton
    if _email_provider_override is not None:
        return _email_provider_override
    if _email_singleton is None:
        _email_singleton = create_email_provider()
    return _email_singleton
