"""Test doubles and fake provider adapters for testing.

These test doubles implement the WhatsAppProvider and EmailProvider ports
for fast, deterministic unit, integration, and contract testing without external network calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.domain.outreach_attempt import OutreachAttempt
from app.ports.providers import (
    EmailProvider,
    ProviderSendResult,
    WhatsAppProvider,
)


class FakeWhatsAppProvider(WhatsAppProvider):
    """Test double for WhatsApp provider port."""

    def __init__(
        self,
        default_success: bool = True,
        should_fail: bool = False,
        failure_code: str = "ERR_MOCK_FAILED",
    ) -> None:
        self.default_success = default_success and not should_fail
        self.should_fail = should_fail
        self.failure_code = failure_code
        self.sent_calls: List[Dict[str, Any]] = []
        self.fail_next_with: Optional[Tuple[str, str]] = None
        self.unknown_next: bool = False
        self.recovery_required_next: bool = False

    def send_message(
        self,
        attempt: OutreachAttempt,
        recipient_phone: str,
        message_body: str,
        attachment_path: Optional[str] = None,
    ) -> ProviderSendResult:
        call_info = {
            "attempt_id": attempt.id,
            "recipient_phone": recipient_phone,
            "phone": recipient_phone,
            "message_body": message_body,
            "body": message_body,
            "attachment_path": attachment_path,
            "attachment": attachment_path,
        }
        self.sent_calls.append(call_info)

        if self.unknown_next:
            self.unknown_next = False
            return ProviderSendResult.unknown("Simulated network drop before ACK")
        if self.recovery_required_next:
            self.recovery_required_next = False
            return ProviderSendResult.recovery_required("Text sent but PDF attachment crashed")
        if self.fail_next_with:
            code, detail = self.fail_next_with
            self.fail_next_with = None
            return ProviderSendResult.failed(code, detail)
        if self.should_fail:
            return ProviderSendResult.failed(
                failure_code=self.failure_code,
                failure_detail="Simulated fake WhatsApp failure",
            )
        if self.default_success:
            ref = f"wa_ref_{int(datetime.now().timestamp() * 1000)}_{len(self.sent_calls)}"
            return ProviderSendResult.sent(provider_reference=ref)
        return ProviderSendResult.failed("ERR_SEND_FAILED", "Default fake failure")


class FakeEmailProvider(EmailProvider):
    """Test double for Email provider port."""

    def __init__(
        self,
        default_success: bool = True,
        should_fail: bool = False,
        failure_code: str = "ERR_MOCK_EMAIL_FAILED",
    ) -> None:
        self.default_success = default_success and not should_fail
        self.should_fail = should_fail
        self.failure_code = failure_code
        self.sent_calls: List[Dict[str, Any]] = []
        self.fail_next_with: Optional[Tuple[str, str]] = None
        self.credential_store: Dict[str, Dict[str, str]] = {}

    def get_sender_credentials(self, sender_account_id: str) -> Dict[str, str]:
        return self.credential_store.get(sender_account_id, {})

    def set_sender_credentials(
        self,
        sender_account_id: str,
        user: str,
        password: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        self.credential_store[sender_account_id] = {
            "user": user,
            "password": password,
            "host": host or "smtp.fake.local",
            "port": str(port or 587),
        }

    def verify_credentials(self, sender_account_id: str) -> Tuple[bool, Optional[str]]:
        if self.should_fail:
            return False, "Simulated fake SMTP authentication error"
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
        call_info = {
            "attempt_id": attempt.id,
            "recipient_email": recipient_email,
            "email": recipient_email,
            "subject": subject,
            "message_body": message_body,
            "body": message_body,
            "attachment_path": attachment_path,
            "attachment": attachment_path,
        }
        self.sent_calls.append(call_info)

        if self.fail_next_with:
            code, detail = self.fail_next_with
            self.fail_next_with = None
            return ProviderSendResult.failed(code, detail)
        if self.should_fail:
            return ProviderSendResult.failed(
                failure_code=self.failure_code,
                failure_detail="Simulated fake Email failure",
            )
        if self.default_success:
            ref = f"em_ref_{int(datetime.now().timestamp() * 1000)}_{len(self.sent_calls)}"
            return ProviderSendResult.sent(provider_reference=ref)
        return ProviderSendResult.failed("ERR_SMTP_AUTH", "SMTP Auth Failed")


# Backward compatible aliases for test suites
MockWhatsAppProvider = FakeWhatsAppProvider
MockEmailProvider = FakeEmailProvider
