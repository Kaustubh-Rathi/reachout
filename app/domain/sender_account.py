"""Sender Account domain entity.

Supports arbitrary N independent sender accounts for WhatsApp, Email, or future
channels without hardcoded limits or singleton assumptions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.domain.enums import Channel, SenderStatus


@dataclass
class SenderAccount:
    """Represents an independent sender identity and its credentials/session context.

    Attributes:
        id: Stable unique identifier for this sender account.
        channel: Communication channel (WHATSAPP, EMAIL).
        provider: Provider driver key (e.g. 'whatsapp_web', 'smtp_gmail', 'aws_ses').
        identity: Sender address/number (e.g. '+919999999999', 'kaustubh@example.com').
        display_name: Human-friendly label (e.g. 'Primary Work Gmail', 'Outreach SIM 2').
        status: Operational status (ACTIVE, INACTIVE, RATE_LIMITED, etc.).
        credential_ref: Secret store or environment variable identifier.
        session_ref: Storage reference for persistent session files / auth cookies.
        created_at: Entity creation timestamp.
        last_used_at: Timestamp when this sender last dispatched an attempt.
        daily_limit: Optional maximum number of messages per rolling 24h.
        hourly_limit: Optional maximum number of messages per rolling hour.
    """

    id: str
    channel: Channel
    provider: str
    identity: str
    display_name: str
    status: SenderStatus = SenderStatus.ACTIVE
    credential_ref: Optional[str] = None
    session_ref: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: Optional[datetime] = None
    daily_limit: Optional[int] = None
    hourly_limit: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"snd_{self.channel.value.lower()}_{uuid.uuid4().hex[:12]}"

    @classmethod
    def create(
        cls,
        channel: Channel,
        provider: str,
        identity: str,
        display_name: str,
        sender_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
        session_ref: Optional[str] = None,
        daily_limit: Optional[int] = None,
        hourly_limit: Optional[int] = None,
    ) -> SenderAccount:
        sid = sender_id or f"snd_{channel.value.lower()}_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        return cls(
            id=sid,
            channel=channel,
            provider=provider.strip(),
            identity=identity.strip(),
            display_name=display_name.strip(),
            status=SenderStatus.ACTIVE,
            credential_ref=credential_ref,
            session_ref=session_ref,
            created_at=now,
            last_used_at=None,
            daily_limit=daily_limit,
            hourly_limit=hourly_limit,
        )

    def is_available(self) -> bool:
        """Check if account is active and operational for sending."""
        return self.status == SenderStatus.ACTIVE

    def record_usage(self, timestamp: Optional[datetime] = None) -> None:
        """Record dispatch event timestamp."""
        self.last_used_at = timestamp or datetime.now(timezone.utc)

    def mark_status(self, new_status: SenderStatus) -> None:
        """Update operational status."""
        self.status = new_status
