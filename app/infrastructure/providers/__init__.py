"""Messaging provider adapters and factory."""

from app.infrastructure.providers.factory import (
    create_email_provider,
    create_whatsapp_provider,
    get_email_provider,
    get_whatsapp_provider,
    reset_provider_overrides,
    set_email_provider,
    set_whatsapp_provider,
)
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider

__all__ = [
    "WhatsAppSessionManager",
    "PlaywrightWhatsAppProvider",
    "SmtpEmailProvider",
    "create_whatsapp_provider",
    "create_email_provider",
    "get_whatsapp_provider",
    "get_email_provider",
    "set_whatsapp_provider",
    "set_email_provider",
    "reset_provider_overrides",
]
