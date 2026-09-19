"""Shared contact filtering predicates.

Contact-list items and company hierarchies expose slightly different shapes
(status fields vs raw timestamps), so these predicates handle both while
keeping a single source of truth for each filter bucket.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def _sent_via(contact: Mapping[str, Any], status_key: str, time_key: str) -> bool:
    status = contact.get(status_key)
    if status is not None:
        return status == "SENT"
    return bool(contact.get(time_key))


def _not_sent_via(contact: Mapping[str, Any], status_key: str, time_key: str) -> bool:
    status = contact.get(status_key)
    if status is not None:
        return status == "NOT_SENT"
    return not contact.get(time_key)


def matches_send_status(contact: Mapping[str, Any], send_status: str) -> bool:
    """Whether a contact matches the outreach/channel-status bucket."""
    bucket = send_status.upper()
    wa_sent = _sent_via(contact, "whatsapp_status", "last_whatsapp_at")
    em_sent = _sent_via(contact, "email_status", "last_email_at")
    if bucket == "SENT":
        return wa_sent or em_sent
    if bucket == "NOT_SENT":
        return not wa_sent and not em_sent
    if bucket == "WHATSAPP_SENT":
        return wa_sent
    if bucket == "EMAIL_SENT":
        return em_sent
    return True


def matches_priority(contact: Mapping[str, Any], priority: str) -> bool:
    """Whether a contact matches the priority bucket."""
    bucket = priority.upper()
    if bucket in ("INTERESTED", "NOT_INTERESTED"):
        return contact.get("crm_outcome") == bucket
    if bucket == "FOLLOW_UP_DUE":
        return bool(contact.get("follow_up_due"))
    if bucket == "RECENTLY_ACTIVE":
        return bool(
            contact.get("last_activity_at")
            or contact.get("last_contacted")
            or contact.get("last_whatsapp_at")
            or contact.get("last_email_at")
        )
    if bucket == "UNCONTACTED":
        return (
            _not_sent_via(contact, "whatsapp_status", "last_whatsapp_at")
            and _not_sent_via(contact, "email_status", "last_email_at")
            and contact.get("crm_outcome") != "NOT_INTERESTED"
        )
    return True


def apply_contact_filters(
    contacts: Any,
    *,
    send_status: str | None = None,
    priority_filter: str | None = None,
) -> Any:
    """Filter an iterable of contact dicts by send status and priority bucket."""
    items = list(contacts)
    if send_status and send_status != "ALL":
        items = [c for c in items if matches_send_status(c, send_status)]
    if priority_filter and priority_filter != "ALL":
        items = [c for c in items if matches_priority(c, priority_filter)]
    return items


def company_matches_filters(
    hierarchy: Dict[str, Any],
    *,
    channel_status: str | None = None,
    priority_filter: str | None = None,
) -> bool:
    """Whether a company hierarchy matches the channel and priority filters."""
    contacts = hierarchy.get("contacts") or []

    if channel_status and channel_status != "ALL":
        bucket = channel_status.upper()
        covered = hierarchy.get("covered_endpoints", 0)
        if bucket == "SENT" and covered == 0:
            return False
        if bucket == "NOT_SENT" and covered > 0:
            return False
        if bucket == "WHATSAPP_SENT" and not any(c.get("last_whatsapp_at") for c in contacts):
            return False
        if bucket == "EMAIL_SENT" and not any(c.get("last_email_at") for c in contacts):
            return False

    if priority_filter and priority_filter != "ALL":
        return any(matches_priority(c, priority_filter) for c in contacts)

    return True
