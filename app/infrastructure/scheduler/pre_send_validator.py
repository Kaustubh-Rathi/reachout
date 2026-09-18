"""Pre-send validation for outreach attempts.

Encapsulates the sender-session, template-variable, and recipient-handle checks
that must pass before any provider dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate
from app.domain.sender_account import SenderAccount


@dataclass(frozen=True)
class PreSendFailure:
    """A validation failure that must be recorded as a FAILED attempt."""

    code: str
    detail: str
    salt_prefix: str
    attachment_ref: Optional[str] = None


class PreSendValidator:
    """Validates an attempt before dispatch and reports the first failure."""

    @staticmethod
    def validate(
        *,
        sender_account: SenderAccount,
        template: MessageTemplate,
        contact: Contact,
        company: Optional[Company],
        channel: Channel,
        destination: str,
        custom_attachment_path: Optional[str],
    ) -> Optional[PreSendFailure]:
        # 0. Sender-session guard: never dispatch from a non-ACTIVE sender.
        if not sender_account.is_available():
            return PreSendFailure(
                code="ERR_SENDER_NOT_ACTIVE",
                detail=(
                    f"Sender '{sender_account.id}' is not ACTIVE "
                    f"(status={sender_account.status.value}); authenticate/reactivate it before sending."
                ),
                salt_prefix="sender_inactive",
            )

        # 1. Template variable validation before any dispatch.
        validation_errors = template.validate(contact=contact, company=company)
        if validation_errors:
            return PreSendFailure(
                code="ERR_TEMPLATE_VARIABLE_UNRESOLVED",
                detail="; ".join(validation_errors),
                salt_prefix="val_err",
                attachment_ref=custom_attachment_path or template.attachment_ref,
            )

        # 2. Recipient handle validation.
        if channel == Channel.WHATSAPP and (not destination or not destination.strip()):
            return PreSendFailure(
                code="ERR_PHONE_UNAVAILABLE",
                detail="Target contact has no valid phone number for WhatsApp",
                salt_prefix="no_phone",
            )
        if channel == Channel.EMAIL and (not destination or not destination.strip()):
            return PreSendFailure(
                code="ERR_EMAIL_UNAVAILABLE",
                detail="Target contact has no valid email address",
                salt_prefix="no_email",
            )

        return None
