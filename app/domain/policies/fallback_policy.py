"""Channel Fallback Policy.

Coordinates multi-channel fallback decisions when a primary channel is unavailable:
- Preserves all historical attempt records (failed/unavailable attempts are never overwritten).
- Critical safety rule: Ambiguous/UNKNOWN provider outcomes CANNOT trigger automatic
  channel fallback, and must enter operator recovery instead.
- If primary channel handle is missing or definitive channel failure occurred,
  evaluates alternative eligible channels (e.g. WhatsApp -> Email).
"""

from __future__ import annotations

from typing import Container, List, Optional, Sequence

from app.domain.contact import Contact
from app.domain.enums import Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility


# Failure codes where fallback to an alternative channel is safe and allowed (definitive recipient/destination failures)
DEFINITIVE_FALLBACK_REASONS = {
    "MISSING_PHONE_NUMBER",
    "INVALID_PHONE_NUMBER",
    "ERR_PHONE_NOT_ON_WHATSAPP",
    "ERR_NOT_ON_WHATSAPP",
    "ERR_INVALID_RECIPIENT",
    "ERR_PHONE_UNAVAILABLE",
    "ERR_PERMANENT_REJECTION",
    "ERR_RECIPIENT_INVALID",
    "ERR_USER_NOT_FOUND",
    "ERR_NUMBER_NOT_REGISTERED",
    "MISSING_EMAIL_ADDRESS",
    "INVALID_EMAIL_ADDRESS",
    "ERR_EMAIL_UNAVAILABLE",
    "ERR_INVALID_EMAIL",
    "ERR_RECIPIENT_REFUSED",
}

# Ambiguous statuses that MUST NOT trigger automatic fallback
AMBIGUOUS_BLOCKED_STATUSES = {
    OutreachStatus.UNKNOWN,
    OutreachStatus.RECOVERY_REQUIRED,
    OutreachStatus.SENDING,
    OutreachStatus.QUEUED,
}


class ChannelFallbackPolicy:
    """Evaluates safe multi-channel dispatch fallback."""

    @staticmethod
    def is_fallback_safe(status: OutreachStatus, failure_code: Optional[str] = None) -> bool:
        """Check if an attempt outcome is safe for automated fallback."""
        if status in AMBIGUOUS_BLOCKED_STATUSES:
            return False
        if status == OutreachStatus.SENT:
            return False
        if failure_code and ("UNKNOWN" in failure_code or "RECOVERY" in failure_code):
            return False
        return True

    @staticmethod
    def determine_fallback_channel(
        contact: Contact,
        failed_channel: Channel,
        failure_code: Optional[str] = None,
        historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
        suppressed_identifiers: Optional[Container[str]] = None,
    ) -> Optional[Channel]:
        """Determine next eligible channel if fallback from failed_channel is permissible.

        Args:
            contact: Recipient candidate.
            failed_channel: The channel that failed or is unavailable.
            failure_code: Diagnostic failure code.
            historical_attempts: Full attempt history.
            suppressed_identifiers: Optional suppression list.

        Returns:
            The fallback Channel if eligible, or None.
        """
        # 1. Fallback candidate channel
        fallback_channel = Channel.EMAIL if failed_channel == Channel.WHATSAPP else Channel.WHATSAPP

        # 2. Check if contact has any ambiguous in-flight or recovery attempt
        if historical_attempts:
            for att in historical_attempts:
                if att.contact_id == contact.contact_id and att.status in AMBIGUOUS_BLOCKED_STATUSES:
                    return None

        # 3. Evaluate eligibility for candidate fallback channel
        elig = evaluate_automatic_eligibility(
            contact=contact,
            channel=fallback_channel,
            historical_attempts=historical_attempts,
            suppressed_identifiers=suppressed_identifiers,
        )

        return fallback_channel if elig.is_eligible else None
