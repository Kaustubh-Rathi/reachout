"""Candidate selection for campaign dispatch.

Computes the per-contact attempt lookup and the Company-First prioritized
candidate list for a preferred channel. Kept separate from the campaign loop's
control flow.
"""

from __future__ import annotations

from typing import Container, Dict, List, Optional, Sequence

from app.domain.contact import Contact
from app.domain.enums import Channel
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.channel_rotation_policy import ChannelRotationPolicy
from app.domain.policies.prioritization import prioritize_company_first


class CandidateSelector:
    """Selects the next prioritized candidates for automatic dispatch."""

    @staticmethod
    def attempts_by_contact(attempts: Sequence[OutreachAttempt]) -> Dict[str, List[OutreachAttempt]]:
        """Group attempts by contact id."""
        grouped: Dict[str, List[OutreachAttempt]] = {}
        for attempt in attempts:
            grouped.setdefault(attempt.contact_id, []).append(attempt)
        return grouped

    @staticmethod
    def select(
        *,
        contacts: Sequence[Contact],
        attempts: Sequence[OutreachAttempt],
        suppressed_identifiers: Optional[Container[str]],
        preferred_channel: Channel,
    ) -> List[Contact]:
        """Return the Company-First prioritized candidate list for the channel."""
        attempts_by_contact = CandidateSelector.attempts_by_contact(attempts)

        def eligibility_check(
            cnt: Contact,
            _attempts=attempts_by_contact,
            _channel=preferred_channel,
            _suppressed=suppressed_identifiers,
        ) -> bool:
            hist = _attempts.get(cnt.contact_id, [])
            decision = ChannelRotationPolicy.evaluate_contact_dispatch(
                contact=cnt,
                preferred_channel=_channel,
                historical_attempts=hist,
                suppressed_identifiers=_suppressed,
            )
            return decision.is_eligible

        return prioritize_company_first(
            contacts=list(contacts),
            eligibility_predicate=eligibility_check,
            dispatched_contact_ids=attempts_by_contact,
        )
