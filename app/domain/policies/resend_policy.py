"""Manual Resend Semantics & Policy.

Enforces rules for manual operator-triggered resends:
- Explicit UI/operator action: RESEND
- Creates a new OutreachAttempt with attempt_type = AttemptType.RESEND
- Generates a unique idempotency key with salt/counter
- Preserves all historical attempts immutably
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.sender_account import SenderAccount


def prepare_manual_resend(
    contact: Contact,
    sender_account: SenderAccount,
    channel: Channel,
    rendered_body: str,
    subject: Optional[str] = None,
    attachment_ref: Optional[str] = None,
    template_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    destination: Optional[str] = None,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    resend_timestamp: Optional[datetime] = None,
) -> OutreachAttempt:
    """Construct a new explicit manual resend attempt.

    Preserves historical attempts without mutating or overwriting them.
    Calculates a distinct sequence-aware or timestamp-seeded idempotency key.
    """
    now = resend_timestamp or datetime.now(timezone.utc)
    
    # Calculate resend sequence count from historical records
    resend_count = 1
    if historical_attempts:
        channel_attempts = [
            a for a in historical_attempts
            if a.contact_id == contact.contact_id and a.channel == channel
        ]
        resend_count = len(channel_attempts) + 1

    idempotency_key = generate_idempotency_key(
        contact_id=contact.contact_id,
        channel=channel,
        attempt_type=AttemptType.RESEND,
        campaign_id=campaign_id,
        attempt_sequence=resend_count,
        custom_salt=str(int(now.timestamp() * 1000)),
        destination=destination,
    )

    return OutreachAttempt.prepare(
        contact_id=contact.contact_id,
        sender_account_id=sender_account.id,
        channel=channel,
        attempt_type=AttemptType.RESEND,
        message_body=rendered_body,
        destination=destination,
        campaign_id=campaign_id,
        template_id=template_id,
        subject=subject,
        attachment_ref=attachment_ref,
        idempotency_key=idempotency_key,
        prepared_at=now,
    )
