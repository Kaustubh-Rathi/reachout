"""Provider factory and configuration resolver.

Provides explicit configuration of outbound messaging providers (Mock vs Live)
based on OUTREACH_MODE environment setting or programmatic dependency injection.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.providers.playwright_whatsapp_provider import PlaywrightWhatsAppProvider
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.providers.smtp_email_provider import SmtpEmailProvider
from app.infrastructure.security.credential_vault import default_credential_vault
from app.ports.providers import (
    EmailProvider,
    ProviderSendResult,
    ProviderStatusResult,
    WhatsAppProvider,
)


class MockWhatsAppProvider:
    """Safe mock WhatsApp provider adapter for test and development environments."""

    def __init__(self, should_fail: bool = False, failure_code: str = "ERR_MOCK_FAILED") -> None:
        self.should_fail = should_fail
        self.failure_code = failure_code
        self.sent_calls: List[Dict[str, Any]] = []

    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        self.sent_calls.append({
            "attempt_id": attempt.id,
            "recipient_phone": recipient_phone,
            "message_body": message_body,
            "attachment_path": attachment_path,
        })
        if self.should_fail:
            return ProviderSendResult.failed(
                failure_code=self.failure_code,
                failure_detail="Simulated mock WhatsApp failure",
            )
        ref = f"wa_ref_{int(datetime.now().timestamp() * 1000)}"
        return ProviderSendResult.sent(provider_reference=ref)

    def check_status(self, provider_reference: str) -> ProviderStatusResult:
        return ProviderStatusResult(status=OutreachStatus.SENT, detail="Mock status check passed")


class MockEmailProvider:
    """Safe mock Email provider adapter for test and development environments."""

    def __init__(self, should_fail: bool = False, failure_code: str = "ERR_MOCK_EMAIL_FAILED") -> None:
        self.should_fail = should_fail
        self.failure_code = failure_code
        self.sent_calls: List[Dict[str, Any]] = []
        self.credential_store: Dict[str, Dict[str, str]] = {}

    def get_sender_credentials(self, sender_account_id: str) -> Dict[str, str]:
        if sender_account_id in self.credential_store:
            return self.credential_store[sender_account_id]
        vault_creds = default_credential_vault.get_credentials(sender_account_id)
        if vault_creds:
            self.credential_store[sender_account_id] = vault_creds
            return vault_creds
        return {}

    def set_sender_credentials(
        self,
        sender_account_id: str,
        user: str,
        password: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        creds = {
            "user": user,
            "password": password,
            "host": host or "smtp.mock.com",
            "port": str(port or 587),
        }
        self.credential_store[sender_account_id] = creds
        default_credential_vault.save_credentials(sender_account_id, creds)

    def verify_credentials(self, sender_account_id: str) -> tuple[bool, Optional[str]]:
        if self.should_fail:
            return False, "Simulated mock SMTP authentication error"
        creds = self.get_sender_credentials(sender_account_id)
        if creds and creds.get("password") == "invalid_password":
            return False, "Invalid credentials provided"
        if not creds or not creds.get("user") or not creds.get("password"):
            return False, "Missing credentials"
        return True, None

    def send_email(
        self,
        attempt: OutreachAttempt,
        recipient_email: str,
        subject: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        self.sent_calls.append({
            "attempt_id": attempt.id,
            "recipient_email": recipient_email,
            "subject": subject,
            "message_body": message_body,
            "attachment_path": attachment_path,
        })
        if self.should_fail:
            return ProviderSendResult.failed(
                failure_code=self.failure_code,
                failure_detail="Simulated mock Email failure",
            )
        ref = f"em_ref_{int(datetime.now().timestamp() * 1000)}"
        return ProviderSendResult.sent(provider_reference=ref)

    def check_status(self, provider_reference: str) -> ProviderStatusResult:
        return ProviderStatusResult(status=OutreachStatus.SENT, detail="Mock email status check passed")


# Module-level registry & overrides
_outreach_mode_override: Optional[str] = None
_whatsapp_provider_override: Optional[WhatsAppProvider] = None
_email_provider_override: Optional[EmailProvider] = None


def get_outreach_mode() -> str:
    """Resolve current outreach mode: 'mock' (default) or 'live'."""
    if _outreach_mode_override:
        return _outreach_mode_override.lower()
    return os.environ.get("OUTREACH_MODE", "mock").strip().lower()


def set_outreach_mode(mode: str) -> None:
    """Explicitly set outreach mode ('mock' or 'live')."""
    global _outreach_mode_override
    clean = mode.strip().lower()
    if clean not in ("mock", "live"):
        raise ValueError(f"Invalid outreach mode '{mode}'. Must be 'mock' or 'live'.")
    _outreach_mode_override = clean


def set_whatsapp_provider(provider: Optional[WhatsAppProvider]) -> None:
    """Override WhatsApp provider instance for dependency injection in tests."""
    global _whatsapp_provider_override
    _whatsapp_provider_override = provider


def set_email_provider(provider: Optional[EmailProvider]) -> None:
    """Override Email provider instance for dependency injection in tests."""
    global _email_provider_override
    _email_provider_override = provider


def reset_provider_overrides() -> None:
    """Clear all runtime overrides and revert to environment settings."""
    global _outreach_mode_override, _whatsapp_provider_override, _email_provider_override
    _outreach_mode_override = None
    _whatsapp_provider_override = None
    _email_provider_override = None


def create_whatsapp_provider(
    mode: Optional[str] = None,
    session_manager: Optional[WhatsAppSessionManager] = None,
    headless: bool = True,
    timeout_seconds: int = 60,
) -> WhatsAppProvider:
    """Factory creating WhatsApp provider matching mode."""
    target_mode = mode.lower() if mode else get_outreach_mode()
    if target_mode == "live":
        return PlaywrightWhatsAppProvider(
            session_manager=session_manager,
            headless=headless,
            timeout_seconds=timeout_seconds,
        )
    return MockWhatsAppProvider()


def create_email_provider(
    mode: Optional[str] = None,
    credential_store: Optional[Dict[str, Dict[str, str]]] = None,
    test_redirect_to: Optional[str] = None,
) -> EmailProvider:
    """Factory creating Email provider matching mode."""
    target_mode = mode.lower() if mode else get_outreach_mode()
    if target_mode == "live":
        return SmtpEmailProvider(
            credential_store=credential_store,
            test_redirect_to=test_redirect_to,
        )
    return MockEmailProvider()


def get_whatsapp_provider() -> WhatsAppProvider:
    """Resolve active WhatsApp provider respecting overrides and OUTREACH_MODE."""
    if _whatsapp_provider_override is not None:
        return _whatsapp_provider_override
    return create_whatsapp_provider()


def get_email_provider() -> EmailProvider:
    """Resolve active Email provider respecting overrides and OUTREACH_MODE."""
    if _email_provider_override is not None:
        return _email_provider_override
    return create_email_provider()
