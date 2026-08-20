"""Automatic Duplicate Prevention & Outreach Eligibility Policy.

Defines deterministic rules for automatic campaigns:
- If a specific endpoint has a successful attempt, do not automatically send to that endpoint again.
- If a contact has remaining uncovered endpoints, those endpoints remain eligible.
- In-flight or unresolved recovery attempts block automatic queueing.
- Suppression lists and opt-out/DNC flags block automatic queueing.
- Invalid or missing phone/email handles block automatic queueing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Container, List, Optional, Sequence, Set

from app.domain.contact import Contact
from app.domain.endpoint import CommunicationEndpoint, extract_endpoints_from_raw, normalize_email_addresses, normalize_phone_numbers
from app.domain.enums import Channel, CRMOutcome, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt


@dataclass(frozen=True)
class EligibilityResult:
    """Evaluation result detailing whether a contact may receive automated outreach."""
    is_eligible: bool
    reason: str
    blocking_attempt_id: Optional[str] = None
    target_endpoint: Optional[CommunicationEndpoint] = None


def is_valid_phone(phone: Optional[str]) -> bool:
    """Validate phone number presence and basic format (single or multi-handle)."""
    if not phone or not phone.strip():
        return False
    normalized = normalize_phone_numbers(phone)
    return len(normalized) > 0


def is_valid_email(email: Optional[str]) -> bool:
    """Validate email address presence and basic structure (single or multi-handle)."""
    if not email or not email.strip():
        return False
    normalized = normalize_email_addresses(email)
    return len(normalized) > 0


def evaluate_automatic_eligibility(
    contact: Contact,
    channel: Channel,
    historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
    suppressed_identifiers: Optional[Container[str]] = None,
    destination: Optional[str] = None,
) -> EligibilityResult:
    """Evaluate whether a contact (or specific endpoint) is eligible for an automated outreach attempt.

    Rules:
    1. Contact must have at least one valid recipient handle for the channel.
    2. Recipient phone, email, or contact ID must not be suppressed or opted out.
    3. Contact must not have active in-flight or recovery-required attempt blocking dispatch.
    4. If destination is provided, check that specific destination.
    5. If destination is not provided, contact is eligible if at least one valid endpoint on the channel remains uncovered.
    """
    # 1. Check recipient handle presence and validity
    if channel == Channel.WHATSAPP:
        if not contact.phone or not contact.phone.strip():
            return EligibilityResult(is_eligible=False, reason="MISSING_PHONE_NUMBER")
        phones = contact.phones if hasattr(contact, "phones") else normalize_phone_numbers(contact.phone)
        if not phones:
            return EligibilityResult(is_eligible=False, reason="INVALID_PHONE_NUMBER")
    elif channel == Channel.EMAIL:
        if not contact.email or not contact.email.strip():
            return EligibilityResult(is_eligible=False, reason="MISSING_EMAIL_ADDRESS")
        emails = contact.emails if hasattr(contact, "emails") else normalize_email_addresses(contact.email)
        if not emails:
            return EligibilityResult(is_eligible=False, reason="INVALID_EMAIL_ADDRESS")

    # 2. Check suppression list and DNC / Opt-Out state
    if suppressed_identifiers is not None:
        identifiers_to_check = [contact.contact_id]
        if contact.phone:
            identifiers_to_check.append(contact.phone.strip())
            for p in (contact.phones if hasattr(contact, "phones") else normalize_phone_numbers(contact.phone)):
                identifiers_to_check.append(p)
        if contact.email:
            identifiers_to_check.append(contact.email.strip().lower())
            for e in (contact.emails if hasattr(contact, "emails") else normalize_email_addresses(contact.email)):
                identifiers_to_check.append(e)

        for ident in identifiers_to_check:
            if ident in suppressed_identifiers:
                return EligibilityResult(is_eligible=False, reason="CONTACT_SUPPRESSED")

    # Check CRM tags for opt-out / DNC
    if contact.tags:
        opt_out_tags = {"dnc", "opt_out", "opt-out", "do_not_contact", "unsubscribed"}
        contact_tags_lower = {t.strip().lower() for t in contact.tags}
        if contact_tags_lower & opt_out_tags:
            return EligibilityResult(is_eligible=False, reason="CONTACT_OPTED_OUT")

    # 3. Check historical attempts for in-flight / recovery blockers on the contact / channel
    if historical_attempts:
        for attempt in historical_attempts:
            if attempt.contact_id != contact.contact_id or attempt.channel != channel:
                continue

            if attempt.status in (
                OutreachStatus.QUEUED,
                OutreachStatus.SENDING,
                OutreachStatus.UNKNOWN,
                OutreachStatus.RECOVERY_REQUIRED,
            ):
                return EligibilityResult(
                    is_eligible=False,
                    reason=f"ATTEMPT_IN_FLIGHT_OR_RECOVERY_{attempt.status.value}",
                    blocking_attempt_id=attempt.id,
                )

    # 4. Check coverage / already sent
    endpoints = [ep for ep in (contact.endpoints if hasattr(contact, "endpoints") else extract_endpoints_from_raw(contact.phone, contact.email)) if ep.channel == channel]

    if destination:
        # Check specific destination
        target_ep = next((ep for ep in endpoints if ep.matches(channel, destination)), None)
        if not target_ep:
            return EligibilityResult(is_eligible=False, reason=f"ENDPOINT_NOT_FOUND_{destination}")

        if historical_attempts:
            for attempt in historical_attempts:
                if attempt.contact_id == contact.contact_id and attempt.channel == channel and attempt.status == OutreachStatus.SENT:
                    att_dest = getattr(attempt, "destination", None)
                    if att_dest and target_ep.matches(channel, att_dest):
                        return EligibilityResult(is_eligible=False, reason=f"ALREADY_SENT_{channel.value}", blocking_attempt_id=attempt.id)
                    elif not att_dest and len(endpoints) == 1:
                        return EligibilityResult(is_eligible=False, reason=f"ALREADY_SENT_{channel.value}", blocking_attempt_id=attempt.id)

        return EligibilityResult(is_eligible=True, reason="ELIGIBLE", target_endpoint=target_ep)

    # General channel check: are there ANY uncovered endpoints on this channel?
    if not historical_attempts:
        # Check legacy single-value timestamps on contact
        if channel == Channel.WHATSAPP and len(endpoints) == 1 and contact.last_whatsapp_at is not None:
            return EligibilityResult(is_eligible=False, reason="ALREADY_SENT_WHATSAPP")
        if channel == Channel.EMAIL and len(endpoints) == 1 and contact.last_email_at is not None:
            return EligibilityResult(is_eligible=False, reason="ALREADY_SENT_EMAIL")
        return EligibilityResult(is_eligible=True, reason="ELIGIBLE", target_endpoint=endpoints[0] if endpoints else None)

    # With historical attempts, see if any endpoint in this channel remains uncontacted
    covered_keys = set()
    blocking_id = None
    for attempt in historical_attempts:
        if attempt.contact_id == contact.contact_id and attempt.channel == channel and attempt.status == OutreachStatus.SENT:
            blocking_id = attempt.id
            att_dest = getattr(attempt, "destination", None)
            if att_dest:
                covered_keys.add(att_dest.strip().lower())
                covered_keys.add(re.sub(r"\D", "", att_dest))
            elif len(endpoints) == 1:
                covered_keys.add(endpoints[0].normalized_address)

    # If contact only has 1 endpoint and last_whatsapp_at/last_email_at is present
    if channel == Channel.WHATSAPP and len(endpoints) == 1 and contact.last_whatsapp_at is not None:
        covered_keys.add(endpoints[0].normalized_address)
    if channel == Channel.EMAIL and len(endpoints) == 1 and contact.last_email_at is not None:
        covered_keys.add(endpoints[0].normalized_address)

    uncovered_eps = [
        ep for ep in endpoints
        if ep.normalized_address not in covered_keys and ep.address not in covered_keys
    ]

    if not uncovered_eps:
        return EligibilityResult(
            is_eligible=False,
            reason=f"ALREADY_SENT_{channel.value}",
            blocking_attempt_id=blocking_id,
        )

    return EligibilityResult(
        is_eligible=True,
        reason="ELIGIBLE",
        target_endpoint=uncovered_eps[0],
    )
