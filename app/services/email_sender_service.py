"""Email sender registration and SMTP credential verification.

Owns the email-specific lifecycle (credential configuration/verification,
auto-indexed sender ids) so the sender inventory service stays focused.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.domain.enums import Channel, SenderStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.sender_account import SenderAccount
from app.ports.infrastructure import EventPublisher
from app.ports.providers import EmailProvider
from app.ports.repositories import SenderRepository


class EmailSenderService:
    """Manages email sender registration and credential verification."""

    def __init__(
        self,
        session: Session,
        repo: SenderRepository,
        email_provider: EmailProvider,
        event_publisher: EventPublisher,
    ) -> None:
        self.session = session
        self.repo = repo
        self.email_provider = email_provider
        self.event_publisher = event_publisher

    def configure_email_sender(
        self,
        id: str,
        identity: str,
        display_name: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        verify_now: bool = True,
    ) -> Dict[str, Any]:
        """Create/register an Email sender and verify its SMTP connection.

        If SMTP verification fails, an exception is raised and NOTHING is persisted;
        no dummy/placeholder row is ever created.
        """
        clean_id = id.strip()
        clean_identity = identity.strip()
        clean_display = display_name.strip()
        clean_user = (user or clean_identity).strip()

        if not clean_identity:
            raise ValidationError("Email address (identity) is required.")

        if not clean_id:
            clean_id = self._next_email_session_id()

        provider = self.email_provider

        if verify_now and clean_user and password:
            provider.set_sender_credentials(
                sender_account_id=clean_id,
                user=clean_user,
                password=password or "",
                host=host,
                port=port,
            )
            success, err = provider.verify_credentials(clean_id)
            if not success:
                raise ValidationError(f"SMTP connection failed: {err or 'could not connect'}")
        else:
            if not clean_user or not password:
                raise ValidationError("SMTP username and password are required.")
            if verify_now and not host:
                raise ValidationError("SMTP host is required to verify the connection.")

        sender = self.repo.get_by_id(clean_id)
        if not sender:
            sender = SenderAccount(
                id=clean_id,
                channel=Channel.EMAIL,
                provider="smtp",
                identity=clean_identity,
                display_name=clean_display,
                status=SenderStatus.ACTIVE,
                daily_limit=100,
                hourly_limit=20,
            )
        else:
            sender.identity = clean_identity
            sender.display_name = clean_display
            sender.status = SenderStatus.ACTIVE

        provider.set_sender_credentials(
            sender_account_id=clean_id,
            user=clean_user,
            password=password or "",
            host=host,
            port=port,
        )

        self.repo.save(sender)
        self.session.commit()
        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": sender.id,
                "channel": "EMAIL",
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )

        return {
            "id": sender.id,
            "channel": "EMAIL",
            "identity": sender.identity,
            "display_name": sender.display_name,
            "status": sender.status.value,
            "verified": True,
            "error_message": None,
        }

    def _next_email_session_id(self) -> str:
        """Return the next available ``EMAIL_SESSION_N`` id."""
        max_idx = 0
        for s in self.repo.list_by_channel(Channel.EMAIL):
            match = re.match(r"(?:EMAIL_)?session_(\d+)", s.id, flags=re.IGNORECASE)
            if match:
                max_idx = max(max_idx, int(match.group(1)))
        return f"EMAIL_SESSION_{max_idx + 1}"

    def verify_email_sender(self, sender_id: str) -> Dict[str, Any]:
        """Verify SMTP credentials for an existing email sender account."""
        sender = self.repo.get_by_id(sender_id)
        if not sender or sender.channel != Channel.EMAIL:
            raise NotFoundError(f"Email sender '{sender_id}' not found")

        success, err = self.email_provider.verify_credentials(sender_id)
        if success:
            sender.status = SenderStatus.ACTIVE
            verification_error = None
        else:
            sender.status = SenderStatus.ERROR
            verification_error = err

        self.repo.save(sender)
        self.session.commit()
        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": sender.id,
                "channel": "EMAIL",
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )

        return {
            "id": sender.id,
            "channel": "EMAIL",
            "status": sender.status.value,
            "verified": sender.status == SenderStatus.ACTIVE,
            "error_message": verification_error,
        }
