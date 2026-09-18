"""Outreach Attempt domain entity.

Maintains immutable historical audit records of all message dispatch attempts,
including exact rendered snapshots, state machine transitions, side-effect tracking,
and recovery mechanics for external distributed system uncertainties.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.errors import ValidationError


def generate_idempotency_key(
    contact_id: str,
    channel: Channel,
    attempt_type: AttemptType,
    campaign_id: Optional[str] = None,
    attempt_sequence: int = 1,
    custom_salt: Optional[str] = None,
    destination: Optional[str] = None,
) -> str:
    """Generate a deterministic idempotency key for an outreach attempt.

    Prevents duplicate submissions to message providers.
    For manual resends, `attempt_sequence` or `custom_salt` distinguishes
    consecutive deliberate resends.
    """
    camp_str = campaign_id or "direct"
    salt_str = custom_salt or f"seq_{attempt_sequence}"
    dest_str = f":{destination.strip().lower()}" if destination else ""
    seed = f"{contact_id}:{channel.value}:{attempt_type.value}:{camp_str}:{salt_str}{dest_str}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"idemp_{channel.value.lower()}_{digest}"


@dataclass
class OutreachAttempt:
    """Represents a discrete historical attempt to deliver a message to a contact.

    Attributes:
        id: Unique attempt identifier.
        contact_id: Target contact entity reference.
        sender_account_id: Sender identity reference.
        channel: Delivery channel (WHATSAPP, EMAIL).
        attempt_type: Trigger mechanism (AUTOMATIC, MANUAL, RESEND).
        status: Current lifecycle/execution state.
        idempotency_key: Unique key preventing duplicate dispatch side-effects.
        message_body_snapshot: Exact body text dispatched at attempt preparation.
        destination: Exact recipient handle (phone number or email address) contacted.
        campaign_id: Optional campaign reference if dispatched within a batch.
        template_id: Optional message template reference.
        subject_snapshot: Email subject line at dispatch time.
        attachment_snapshot: Attachment path/reference at dispatch time.
        prepared_at: Timestamp when attempt was generated.
        started_at: Timestamp when dispatch execution began.
        completed_at: Timestamp when terminal/recovery state was recorded.
        failure_code: Machine-readable error code if failed (e.g. 'ERR_NOT_ON_WHATSAPP').
        failure_detail: Detailed provider diagnostic message.
        provider_reference: Provider-assigned message ID or tracking token.
        recovery_notes: Diagnostics recorded during recovery audit.
    """

    id: str
    contact_id: str
    sender_account_id: str
    channel: Channel
    attempt_type: AttemptType
    status: OutreachStatus
    idempotency_key: str
    message_body_snapshot: str
    destination: Optional[str] = None
    campaign_id: Optional[str] = None
    template_id: Optional[str] = None
    subject_snapshot: Optional[str] = None
    attachment_snapshot: Optional[str] = None
    prepared_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_code: Optional[str] = None
    failure_detail: Optional[str] = None
    provider_reference: Optional[str] = None
    recovery_notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"att_{self.channel.value.lower()}_{uuid.uuid4().hex[:16]}"
        if not self.idempotency_key:
            self.idempotency_key = generate_idempotency_key(
                contact_id=self.contact_id,
                channel=self.channel,
                attempt_type=self.attempt_type,
                campaign_id=self.campaign_id,
                destination=self.destination,
            )

    @classmethod
    def prepare(
        cls,
        contact_id: str,
        sender_account_id: str,
        channel: Channel,
        attempt_type: AttemptType,
        message_body: str,
        destination: Optional[str] = None,
        campaign_id: Optional[str] = None,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        prepared_at: Optional[datetime] = None,
    ) -> OutreachAttempt:
        """Factory method to construct a fresh PREPARED outreach attempt."""
        now = prepared_at or datetime.now(timezone.utc)
        key = idempotency_key or generate_idempotency_key(
            contact_id=contact_id,
            channel=channel,
            attempt_type=attempt_type,
            campaign_id=campaign_id,
            destination=destination,
            custom_salt=str(int(now.timestamp() * 1000)) if attempt_type == AttemptType.RESEND else None,
        )
        return cls(
            id=f"att_{channel.value.lower()}_{uuid.uuid4().hex[:16]}",
            contact_id=contact_id,
            campaign_id=campaign_id,
            sender_account_id=sender_account_id,
            channel=channel,
            template_id=template_id,
            attempt_type=attempt_type,
            status=OutreachStatus.PREPARED,
            idempotency_key=key,
            message_body_snapshot=message_body,
            destination=destination,
            subject_snapshot=subject,
            attachment_snapshot=attachment_ref,
            prepared_at=now,
        )

    def mark_queued(self) -> None:
        """Mark attempt as queued in scheduler/pipeline."""
        if self.status != OutreachStatus.PREPARED:
            raise ValidationError(f"Cannot queue attempt from status '{self.status}'")
        self.status = OutreachStatus.QUEUED

    def mark_sending(self, timestamp: Optional[datetime] = None) -> None:
        """Mark attempt as actively executing with provider."""
        if self.status not in (OutreachStatus.PREPARED, OutreachStatus.QUEUED):
            raise ValidationError(f"Cannot begin sending from status '{self.status}'")
        self.status = OutreachStatus.SENDING
        self.started_at = timestamp or datetime.now(timezone.utc)

    def mark_sent(
        self,
        provider_reference: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record successful message delivery confirmation."""
        now = timestamp or datetime.now(timezone.utc)
        self.status = OutreachStatus.SENT
        self.completed_at = now
        if provider_reference:
            self.provider_reference = provider_reference

    def mark_failed(
        self,
        failure_code: str,
        failure_detail: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record definitive delivery failure."""
        now = timestamp or datetime.now(timezone.utc)
        self.status = OutreachStatus.FAILED
        self.failure_code = failure_code
        self.failure_detail = failure_detail
        self.completed_at = now

    def mark_unknown(
        self,
        reason: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record ambiguous delivery state when external status is unknown."""
        now = timestamp or datetime.now(timezone.utc)
        self.status = OutreachStatus.UNKNOWN
        self.failure_code = "ERR_EXTERNAL_STATUS_UNKNOWN"
        self.failure_detail = reason
        self.completed_at = now

    def mark_recovery_required(
        self,
        reason: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Flag that process crashed or timed out mid-flight, requiring manual/automated audit."""
        now = timestamp or datetime.now(timezone.utc)
        self.status = OutreachStatus.RECOVERY_REQUIRED
        self.failure_code = "ERR_RECOVERY_REQUIRED"
        self.failure_detail = reason
        self.completed_at = now

    def resolve_recovery(
        self,
        resolved_status: OutreachStatus,
        notes: str,
        provider_reference: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Resolve a recovery-required attempt following audit."""
        if self.status not in (OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED):
            raise ValidationError(f"Cannot resolve recovery for attempt with status '{self.status}'")
        if resolved_status not in (OutreachStatus.SENT, OutreachStatus.FAILED):
            raise ValidationError(f"Resolved recovery status must be SENT or FAILED, got '{resolved_status}'")
        now = timestamp or datetime.now(timezone.utc)
        self.status = resolved_status
        self.recovery_notes = notes
        if provider_reference:
            self.provider_reference = provider_reference
        self.completed_at = now
