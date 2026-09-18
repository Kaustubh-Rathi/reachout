"""Per-channel presentation defaults (labels, recipient fields, default copy).

Centralizes the channel-specific branching used by the outreach flows so a new
channel is described in one place instead of being spread across conditionals.
"""

from __future__ import annotations

from typing import Dict, Optional

from app.config import DEFAULT_MESSAGE_BODY, DEFAULT_MESSAGE_SUBJECT
from app.domain.contact import Contact
from app.domain.enums import Channel

CHANNEL_LABELS: Dict[Channel, str] = {
    Channel.WHATSAPP: "WhatsApp",
    Channel.EMAIL: "Email",
}


def channel_label(channel: Channel) -> str:
    """Human-readable channel label."""
    return CHANNEL_LABELS.get(channel, channel.value.title())


def recipient_for(contact: Contact, channel: Channel) -> Optional[str]:
    """Return the contact's primary destination for the channel, if any."""
    if channel == Channel.WHATSAPP:
        return contact.primary_phone
    return contact.primary_email


def default_body(contact: Contact, channel: Channel, *, is_resend: bool = False) -> str:
    """Channel-agnostic default body when neither template nor custom body is supplied."""
    if is_resend:
        if channel == Channel.EMAIL:
            return (
                f"Hi {contact.first_name},\n\nFollowing up on my previous note regarding technical roles "
                f"at {contact.company_id}.\n\nBest regards,\nCandidate"
            )
        return f"Hi {contact.first_name}, following up regarding opportunities at {contact.company_id}."
    return DEFAULT_MESSAGE_BODY.format(first_name=contact.first_name or "", company=contact.company_id or "")


def default_subject(contact: Contact, *, is_resend: bool = False) -> str:
    """Default email subject line."""
    if is_resend:
        return f"Following up: Opportunities at {contact.company_id}"
    return f"{DEFAULT_MESSAGE_SUBJECT} at {contact.company_id}"
