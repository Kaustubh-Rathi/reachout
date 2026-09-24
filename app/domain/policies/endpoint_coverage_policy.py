"""Contact Endpoint Coverage and Historical Tracking Policy.

Answers domain questions regarding:
- Which exact endpoints remain uncovered for a contact?
- Has a specific phone number or email address already been successfully contacted?
- Is the contact completely covered across all intended endpoints?
- Which specific endpoint should be targeted next?
"""

from __future__ import annotations

from typing import Any, Container, Dict, List, Optional, Sequence, Set

from app.domain.contact import Contact
from app.domain.endpoint import CommunicationEndpoint
from app.domain.enums import Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt

# Ambiguous or active states that block automatic duplicate outreach
BLOCKING_INFLIGHT_STATUSES = {
    OutreachStatus.PREPARED,
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
                if len(contact.phones) <= 1:
                    return True
            elif endpoint.channel == Channel.EMAIL and endpoint.ordinal == 0 and contact.last_email_at is not None:
                if len(contact.emails) <= 1:
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
        attempt_dest = attempt.destination
        if attempt_dest:
            if endpoint.matches(attempt.channel, attempt_dest):
                return True
        else:
            if contact is not None:
                channel_endpoints = [ep for ep in contact.endpoints if ep.channel == endpoint.channel]
                if len(channel_endpoints) <= 1 and endpoint.ordinal == 0:
                    return True
            else:
                if endpoint.ordinal == 0:
                    return True

    return False


def _matching_endpoint_attempts(
    endpoint: CommunicationEndpoint,
    contact_id: str,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
) -> List[OutreachAttempt]:
    if not historical_attempts:
        return []
    return [
        attempt
        for attempt in historical_attempts
        if attempt.contact_id == contact_id
        and attempt.channel == endpoint.channel
        and (endpoint.matches(endpoint.channel, attempt.destination) if attempt.destination else endpoint.ordinal == 0)
    ]


DEFINITIVE_ENDPOINT_FAILURES: Set[str] = {
    "ERR_NOT_ON_WHATSAPP",
    "ERR_PHONE_NOT_ON_WHATSAPP",
    "ERR_PHONE_UNAVAILABLE",
    "ERR_INVALID_RECIPIENT",
    "INVALID_PHONE_NUMBER",
    "ERR_INVALID_PHONE_FORMAT",
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
    "OPERATOR_CANCELLED",
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
            dest = attempt.destination
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

    contact_id = contact.contact_id if isinstance(contact, Contact) else str(contact)

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

    all_endpoints = contact.endpoints

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
    all_endpoints = contact.endpoints
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
    suppressed_identifiers: Optional[Container[str]] = None,
) -> Dict[str, Any]:
    """Generate detailed per-endpoint coverage audit metrics for UI, API, and dashboards."""
    all_endpoints = contact.endpoints
    suppressed = suppressed_identifiers or set()
    contact_blocked = has_ambiguous_or_inflight_blocker(contact, historical_attempts)
    opt_out_tags = {"dnc", "opt_out", "opt-out", "do_not_contact", "unsubscribed"}
    contact_excluded = contact.contact_id in suppressed or bool(
        {tag.strip().lower() for tag in (contact.tags or [])} & opt_out_tags
    )

    wa_endpoints = []
    em_endpoints = []

    for ep in all_endpoints:
        matching_attempts = _matching_endpoint_attempts(ep, contact.contact_id, historical_attempts)
        matching_attempts.sort(key=lambda a: a.completed_at or a.prepared_at, reverse=True)
        attempt = matching_attempts[0] if matching_attempts else None
        sent_attempt = next((a for a in matching_attempts if a.status == OutreachStatus.SENT), None)
        failed_attempt = next((a for a in matching_attempts if a.status == OutreachStatus.FAILED), None)
        is_sent = is_endpoint_covered(ep, contact.contact_id, historical_attempts, contact)
        is_permanent_failure = is_endpoint_permanently_failed(ep, contact.contact_id, historical_attempts)
        is_excluded = contact_excluded or ep.address in suppressed or ep.normalized_address in suppressed

        if is_sent:
            coverage_state = "SENT"
        elif is_permanent_failure:
            coverage_state = "PERMANENT_FAILED"
        elif is_excluded:
            coverage_state = "SUPPRESSED"
        elif contact_blocked:
            coverage_state = "BLOCKED"
        elif not matching_attempts:
            coverage_state = "NEVER_ATTEMPTED"
        elif failed_attempt:
            coverage_state = "RETRYABLE"
        else:
            coverage_state = "READY"

        status_str = "SENT" if is_sent else ("FAILED" if failed_attempt else "NOT_CONTACTED")
        if attempt and attempt.status in BLOCKING_INFLIGHT_STATUSES and not is_sent:
            status_str = attempt.status.value

        selected_attempt = sent_attempt or attempt

        ep_info = {
            "channel": ep.channel.value,
            "address": ep.address,
            "normalized_address": ep.normalized_address,
            "ordinal": ep.ordinal,
            "status": status_str,
            "coverage_state": coverage_state,
            "is_covered": is_sent,
            "is_ready": coverage_state in {"READY", "NEVER_ATTEMPTED", "RETRYABLE"},
            "is_blocked": coverage_state == "BLOCKED",
            "is_permanently_failed": coverage_state == "PERMANENT_FAILED",
            "is_excluded": coverage_state == "SUPPRESSED",
            "never_attempted": not matching_attempts,
            "attempt_count": len(matching_attempts),
            "failed_attempt_count": sum(1 for a in matching_attempts if a.status == OutreachStatus.FAILED),
            "sender_account_id": selected_attempt.sender_account_id if selected_attempt else None,
            "template_id": selected_attempt.template_id if selected_attempt else None,
            "attempt_id": selected_attempt.id if selected_attempt else None,
            "failure_code": failed_attempt.failure_code if failed_attempt else None,
            "failure_detail": failed_attempt.failure_detail if failed_attempt else None,
            "last_failure_at": failed_attempt.completed_at.isoformat()
            if failed_attempt and failed_attempt.completed_at
            else None,
            "sent_at": (sent_attempt.completed_at or sent_attempt.started_at or sent_attempt.prepared_at).isoformat()
            if (sent_attempt and (sent_attempt.completed_at or sent_attempt.started_at or sent_attempt.prepared_at))
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
    all_endpoint_rows = wa_endpoints + em_endpoints
    is_complete = total > 0 and covered == total
    dispatch_complete = total > 0 and all(
        endpoint["coverage_state"] in {"SENT", "PERMANENT_FAILED", "SUPPRESSED"} for endpoint in all_endpoint_rows
    )

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
        "ready_endpoints": sum(1 for e in wa_endpoints + em_endpoints if e["is_ready"]),
        "blocked_endpoints": sum(1 for e in wa_endpoints + em_endpoints if e["is_blocked"]),
        "permanent_failed_endpoints": sum(1 for e in wa_endpoints + em_endpoints if e["is_permanently_failed"]),
        "excluded_endpoints": sum(1 for e in wa_endpoints + em_endpoints if e["is_excluded"]),
        "never_attempted_endpoints": sum(1 for e in wa_endpoints + em_endpoints if e["never_attempted"]),
        "retryable_failed_endpoints": sum(
            1
            for e in wa_endpoints + em_endpoints
            if e["coverage_state"] == "RETRYABLE" and e["failed_attempt_count"] > 0
        ),
        "is_fully_covered": is_complete,
        "is_dispatch_complete": dispatch_complete,
        "has_uncovered_whatsapp": wa_covered < wa_total,
        "has_uncovered_email": em_covered < em_total,
    }
