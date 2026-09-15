"""Contact Endpoint Coverage and Historical Tracking Policy.

Answers domain questions regarding:
- Which exact endpoints remain uncovered for a contact?
- Has a specific phone number or email address already been successfully contacted?
- Is the contact completely covered across all intended endpoints?
- Which specific endpoint should be targeted next?
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Container, Dict, List, Optional, Sequence, Set

from app.domain.contact import Contact
from app.domain.endpoint import CommunicationEndpoint, extract_endpoints_from_raw
from app.domain.enums import Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt

# Ambiguous or active states that block automatic duplicate outreach
BLOCKING_INFLIGHT_STATUSES = {
    OutreachStatus.QUEUED,
    OutreachStatus.SENDING,
    OutreachStatus.UNKNOWN,
    OutreachStatus.RECOVERY_REQUIRED,
}


def is_endpoint_covered(
    endpoint: CommunicationEndpoint,
    contact_id_or_history: Any,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    contact: Optional[Contact] = None,
) -> bool:
    """Check if a specific communication endpoint has been successfully covered by a terminal SENT dispatch."""
    if isinstance(contact_id_or_history, str):
        cid = contact_id_or_history
        hist = historical_attempts
    elif isinstance(contact_id_or_history, (list, tuple)):
        cid = None
        hist = contact_id_or_history
    else:
        cid = None
        hist = None

    if not hist:
        # Fallback to Contact model timestamps if no historical attempts provided
        if contact is not None:
            if endpoint.channel == Channel.WHATSAPP and endpoint.ordinal == 0 and contact.last_whatsapp_at is not None:
                phones = contact.phones if hasattr(contact, "phones") else []
                if len(phones) <= 1:
                    return True
            elif endpoint.channel == Channel.EMAIL and endpoint.ordinal == 0 and contact.last_email_at is not None:
                emails = contact.emails if hasattr(contact, "emails") else []
                if len(emails) <= 1:
                    return True
        return False

    # Check historical attempts
    for attempt in hist:
        if cid and attempt.contact_id != cid:
            continue
        if attempt.channel != endpoint.channel:
            continue

        if attempt.status != OutreachStatus.SENT:
            continue

        # Match destination
        attempt_dest = getattr(attempt, "destination", None)
        if attempt_dest:
            if endpoint.matches(attempt.channel, attempt_dest):
                return True
        else:
            if contact is not None:
                channel_endpoints = (
                    [ep for ep in contact.endpoints if ep.channel == endpoint.channel]
                    if hasattr(contact, "endpoints")
                    else []
                )
                if len(channel_endpoints) <= 1 and endpoint.ordinal == 0:
                    return True
            else:
                if endpoint.ordinal == 0:
                    return True

    return False


def get_endpoint_attempt_info(
    endpoint: CommunicationEndpoint,
    contact_id: str,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
) -> Optional[OutreachAttempt]:
    """Retrieve the most recent successful or terminal OutreachAttempt for this endpoint."""
    if not historical_attempts:
        return None

    matching_attempts = []
    for att in historical_attempts:
        if att.contact_id != contact_id or att.channel != endpoint.channel:
            continue
        dest = getattr(att, "destination", None)
        if dest and endpoint.matches(att.channel, dest):
            matching_attempts.append(att)
        elif not dest and endpoint.ordinal == 0:
            matching_attempts.append(att)

    if not matching_attempts:
        return None

    # Return newest attempt by timestamp
    matching_attempts.sort(key=lambda a: a.completed_at or a.prepared_at, reverse=True)
    return matching_attempts[0]


DEFINITIVE_ENDPOINT_FAILURES: Set[str] = {
    "ERR_NOT_ON_WHATSAPP",
    "ERR_PHONE_NOT_ON_WHATSAPP",
    "ERR_PHONE_UNAVAILABLE",
    "ERR_INVALID_RECIPIENT",
    "INVALID_PHONE_NUMBER",
    "MISSING_PHONE_NUMBER",
    "ERR_PERMANENT_REJECTION",
    "ERR_EMAIL_UNAVAILABLE",
    "INVALID_EMAIL_ADDRESS",
    "MISSING_EMAIL_ADDRESS",
    "ERR_RECIPIENT_INVALID",
    "ERR_USER_NOT_FOUND",
    "ERR_NUMBER_NOT_REGISTERED",
    "ERR_INVALID_EMAIL",
    "ERR_RECIPIENT_REFUSED",
}


def is_endpoint_permanently_failed(
    endpoint: CommunicationEndpoint,
    contact_id: str,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
) -> bool:
    """Check if an endpoint has a definitive permanent failure that excludes it from automatic retry."""
    if not historical_attempts:
        return False

    for attempt in historical_attempts:
        if attempt.contact_id != contact_id or attempt.channel != endpoint.channel:
            continue
        if attempt.status == OutreachStatus.FAILED:
            dest = getattr(attempt, "destination", None)
            matches = endpoint.matches(attempt.channel, dest) if dest else (endpoint.ordinal == 0)
            if matches and attempt.failure_code in DEFINITIVE_ENDPOINT_FAILURES:
                return True

    return False


def has_ambiguous_or_inflight_blocker(
    contact: Any,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    channel: Optional[Channel] = None,
) -> bool:
    """Check if the contact has any in-flight, unconfirmed, or recovery-required attempt blocking dispatch."""
    if not historical_attempts:
        return False

    contact_id = contact.contact_id if hasattr(contact, "contact_id") else str(contact)

    for attempt in historical_attempts:
        if attempt.contact_id != contact_id:
            continue
        if channel is not None and attempt.channel != channel:
            continue
        if attempt.status in BLOCKING_INFLIGHT_STATUSES:
            return True

    return False


def get_uncovered_endpoints(
    contact: Contact,
    channel_or_history: Any = None,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    suppressed_identifiers: Optional[Container[str]] = None,
    channel: Optional[Channel] = None,
) -> List[CommunicationEndpoint]:
    """Return all valid, unsuppressed, uncovered endpoints for a contact in deterministic ordinal order."""
    if isinstance(channel_or_history, Channel):
        target_channel = channel_or_history
        hist = historical_attempts
    elif isinstance(channel_or_history, (list, tuple)):
        target_channel = channel
        hist = channel_or_history
    else:
        target_channel = channel
        hist = historical_attempts

    all_endpoints = (
        contact.endpoints if hasattr(contact, "endpoints") else extract_endpoints_from_raw(contact.phone, contact.email)
    )

    uncovered: List[CommunicationEndpoint] = []

    # Check suppression
    suppressed = suppressed_identifiers or set()
    if contact.contact_id in suppressed:
        return []

    # Check contact DNC / Opt-Out tags
    if contact.tags:
        opt_out_tags = {"dnc", "opt_out", "opt-out", "do_not_contact", "unsubscribed"}
        if {t.strip().lower() for t in contact.tags} & opt_out_tags:
            return []

    for ep in all_endpoints:
        if target_channel is not None and ep.channel != target_channel:
            continue

        # Check suppression for this address
        if ep.normalized_address in suppressed or ep.address in suppressed:
            continue

        # Check if already covered
        if is_endpoint_covered(ep, contact.contact_id, hist, contact):
            continue

        # Check if permanently failed (definitive failure)
        if is_endpoint_permanently_failed(ep, contact.contact_id, hist):
            continue

        uncovered.append(ep)

    return uncovered


def is_contact_fully_covered(
    contact: Contact,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    suppressed_identifiers: Optional[Container[str]] = None,
) -> bool:
    """Determine if all valid intended communication endpoints for a contact have been successfully covered."""
    all_endpoints = (
        contact.endpoints if hasattr(contact, "endpoints") else extract_endpoints_from_raw(contact.phone, contact.email)
    )
    if not all_endpoints:
        return True  # No valid endpoints exist, cannot contact

    uncovered = get_uncovered_endpoints(
        contact=contact,
        channel_or_history=None,
        historical_attempts=historical_attempts,
        suppressed_identifiers=suppressed_identifiers,
    )
    return len(uncovered) == 0


def get_next_uncovered_endpoint(
    contact: Contact,
    channel_or_history: Any = None,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    suppressed_identifiers: Optional[Container[str]] = None,
    channel: Optional[Channel] = None,
) -> Optional[CommunicationEndpoint]:
    """Return the next uncovered endpoint for the specified channel in deterministic original order."""
    if isinstance(channel_or_history, Channel):
        target_channel = channel_or_history
        hist = historical_attempts
    elif isinstance(channel_or_history, (list, tuple)):
        target_channel = channel
        hist = channel_or_history
    else:
        target_channel = channel
        hist = historical_attempts

    uncovered = get_uncovered_endpoints(
        contact=contact,
        channel_or_history=target_channel,
        historical_attempts=hist,
        suppressed_identifiers=suppressed_identifiers,
    )
    return uncovered[0] if uncovered else None


def get_contact_endpoint_metrics(
    contact: Contact,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
) -> Dict[str, Any]:
    """Generate detailed per-endpoint coverage audit metrics for UI, API, and dashboards."""
    all_endpoints = (
        contact.endpoints if hasattr(contact, "endpoints") else extract_endpoints_from_raw(contact.phone, contact.email)
    )

    wa_endpoints = []
    em_endpoints = []

    for ep in all_endpoints:
        attempt = get_endpoint_attempt_info(ep, contact.contact_id, historical_attempts)
        is_sent = is_endpoint_covered(ep, contact.contact_id, historical_attempts, contact)

        status_str = (
            "SENT"
            if is_sent
            else ("FAILED" if attempt and attempt.status == OutreachStatus.FAILED else "NOT_CONTACTED")
        )
        if attempt and attempt.status in BLOCKING_INFLIGHT_STATUSES:
            status_str = attempt.status.value

        ep_info = {
            "channel": ep.channel.value,
            "address": ep.address,
            "normalized_address": ep.normalized_address,
            "ordinal": ep.ordinal,
            "status": status_str,
            "is_covered": is_sent,
            "sender_account_id": attempt.sender_account_id if attempt else None,
            "template_id": attempt.template_id if attempt else None,
            "attempt_id": attempt.id if attempt else None,
            "sent_at": (attempt.completed_at or attempt.started_at or attempt.prepared_at).isoformat()
            if (attempt and is_sent and (attempt.completed_at or attempt.started_at or attempt.prepared_at))
            else None,
        }

        if ep.channel == Channel.WHATSAPP:
            wa_endpoints.append(ep_info)
        else:
            em_endpoints.append(ep_info)

    wa_total = len(wa_endpoints)
    wa_covered = sum(1 for e in wa_endpoints if e["is_covered"])
    em_total = len(em_endpoints)
    em_covered = sum(1 for e in em_endpoints if e["is_covered"])
    total = wa_total + em_total
    covered = wa_covered + em_covered
    is_complete = total > 0 and covered == total

    return {
        "contact_id": contact.contact_id,
        "whatsapp_endpoints": wa_endpoints,
        "email_endpoints": em_endpoints,
        "whatsapp_total": wa_total,
        "whatsapp_covered": wa_covered,
        "email_total": em_total,
        "email_covered": em_covered,
        "total_endpoints": total,
        "covered_endpoints": covered,
        "is_fully_covered": is_complete,
        "has_uncovered_whatsapp": wa_covered < wa_total,
        "has_uncovered_email": em_covered < em_total,
    }
