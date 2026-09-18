"""Provider factory and configuration resolver.

Provides production resolution and test injection for outbound messaging providers.
Production canonical providers:
  WhatsApp -> PlaywrightWhatsAppProvider
  Email    -> SmtpEmailProvider

The canonical provider singletons and test overrides are owned by the composition
root; these functions are the provider-facing facade over it.
"""

from __future__ import annotations

from typing import Dict, Optional

from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.security.credential_vault import CredentialVault
from app.ports.providers import EmailProvider, WhatsAppProvider


def set_whatsapp_provider(provider: Optional[WhatsAppProvider]) -> None:
    """Override WhatsApp provider instance for dependency injection in tests."""
    from app.composition import set_whatsapp_provider as _set

    _set(provider)


def set_email_provider(provider: Optional[EmailProvider]) -> None:
    """Override Email provider instance for dependency injection in tests."""
    from app.composition import set_email_provider as _set

    _set(provider)


def reset_provider_overrides() -> None:
    """Clear all runtime overrides."""
    from app.composition import reset_provider_overrides as _reset

    _reset()


def create_whatsapp_provider(
    session_manager: WhatsAppSessionManager,
    headless: bool = False,
    timeout_seconds: int = 60,
) -> WhatsAppProvider:
    """Factory creating canonical production WhatsApp provider."""
    return PlaywrightWhatsAppProvider(
        session_manager=session_manager,
        headless=headless,
        timeout_seconds=timeout_seconds,
    )


def create_email_provider(
    credential_vault: CredentialVault,
    credential_store: Optional[Dict[str, Dict[str, str]]] = None,
    test_redirect_to: Optional[str] = None,
) -> EmailProvider:
    """Factory creating canonical production Email provider."""
    return SmtpEmailProvider(
        credential_vault=credential_vault,
        credential_store=credential_store,
        test_redirect_to=test_redirect_to,
    )


def get_whatsapp_provider() -> WhatsAppProvider:
    """Resolve the active WhatsApp provider (canonical singleton or test override)."""
    from app.composition import get_whatsapp_provider as _get

    return _get()


def get_email_provider() -> EmailProvider:
    """Resolve the active Email provider (canonical singleton or test override)."""
    from app.composition import get_email_provider as _get

    return _get()
