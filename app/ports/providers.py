"""Provider port interfaces for outbound messaging channels.

Decouples domain outreach operations from specific communication engines (Playwright,
WhatsApp Web, SMTP, SES, Twilio, SendGrid).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt


@dataclass(frozen=True)
class ProviderSendResult:
    """Standardized response from an external messaging provider."""

    success: bool
    status: OutreachStatus
    provider_reference: Optional[str] = None
    failure_code: Optional[str] = None
    failure_detail: Optional[str] = None

    @classmethod
    def sent(cls, provider_reference: Optional[str] = None) -> ProviderSendResult:
        return cls(
            success=True,
            status=OutreachStatus.SENT,
            provider_reference=provider_reference,
        )

    @classmethod
    def failed(cls, failure_code: str, failure_detail: str) -> ProviderSendResult:
        return cls(
            success=False,
            status=OutreachStatus.FAILED,
            failure_code=failure_code,
            failure_detail=failure_detail,
        )

    @classmethod
    def unknown(cls, reason: str) -> ProviderSendResult:
        return cls(
            success=False,
            status=OutreachStatus.UNKNOWN,
            failure_code="ERR_PROVIDER_STATUS_UNKNOWN",
            failure_detail=reason,
        )

    @classmethod
    def recovery_required(cls, reason: str) -> ProviderSendResult:
        return cls(
            success=False,
            status=OutreachStatus.RECOVERY_REQUIRED,
            failure_code="ERR_RECOVERY_REQUIRED",
            failure_detail=reason,
        )


@dataclass(frozen=True)
class ProviderStatusResult:
    """Response when polling or auditing provider status for an existing message."""

    status: OutreachStatus
    detail: Optional[str] = None


@runtime_checkable
class WhatsAppProvider(Protocol):
    """Port for interacting with WhatsApp messaging engines."""

    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        """Dispatch a single WhatsApp text/attachment message."""
        ...


@runtime_checkable
class EmailProvider(Protocol):
    """Port for interacting with Email / SMTP / API engines."""

    def send_email(
        self,
        attempt: OutreachAttempt,
        recipient_email: str,
        subject: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        """Dispatch a single Email."""
        ...

    def get_sender_credentials(self, sender_account_id: str) -> Dict[str, str]:
        """Return stored SMTP credentials for a sender (never persisted in plaintext)."""
        ...

    def set_sender_credentials(
        self,
        sender_account_id: str,
        user: str,
        password: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        """Store encrypted SMTP credentials for a sender."""
        ...

    def verify_credentials(self, sender_account_id: str) -> Tuple[bool, Optional[str]]:
        """Verify stored SMTP credentials, returning (ok, error_detail)."""
        ...
