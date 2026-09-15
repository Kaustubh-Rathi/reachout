"""Channel Rotation and Endpoint Selection Policy.

Implements deterministic multi-channel rotation (e.g. WHATSAPP, WHATSAPP, EMAIL, EMAIL)
while respecting contact endpoint availability, sender health, and ambiguity safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Container, List, Optional, Sequence, Tuple

from app.domain.contact import Contact
from app.domain.endpoint import CommunicationEndpoint
from app.domain.enums import Channel
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.endpoint_coverage_policy import (
    get_next_uncovered_endpoint,
    has_ambiguous_or_inflight_blocker,
    is_contact_fully_covered,
)
from app.domain.sender_account import SenderAccount

DEFAULT_ROTATION_SEQUENCE: List[Channel] = [
    Channel.WHATSAPP,
    Channel.WHATSAPP,
    Channel.EMAIL,
    Channel.EMAIL,
]


@dataclass(frozen=True)
class ChannelDispatchDecision:
    """Outcome of channel and endpoint evaluation for a candidate contact."""

    is_eligible: bool
    channel: Optional[Channel]
    endpoint: Optional[CommunicationEndpoint]
    preferred_channel: Channel
    is_fallback: bool
    reason: str


class ChannelRotationPolicy:
    """Domain policy governing global channel rotation and contact endpoint resolution."""

    @staticmethod
    def build_dynamic_channel_sequence(
        active_senders: Optional[Sequence[SenderAccount]] = None,
        default_sequence: Optional[Sequence[Channel]] = None,
    ) -> List[Channel]:
        """Build a deterministic channel rotation sequence based on active senders or configured defaults."""
        if not active_senders:
            return list(default_sequence or DEFAULT_ROTATION_SEQUENCE)

        wa_count = sum(1 for s in active_senders if s.channel == Channel.WHATSAPP and s.is_available())
        em_count = sum(1 for s in active_senders if s.channel == Channel.EMAIL and s.is_available())

        if wa_count == 0 and em_count == 0:
            return list(default_sequence or DEFAULT_ROTATION_SEQUENCE)
        if wa_count == 0:
            return [Channel.EMAIL]
        if em_count == 0:
            return [Channel.WHATSAPP]

        # Interleave channel blocks according to active sender capacities (defaulting to 2 & 2)
        wa_block = min(max(1, wa_count), 4)
        em_block = min(max(1, em_count), 4)

        sequence: List[Channel] = []
        sequence.extend([Channel.WHATSAPP] * wa_block)
        sequence.extend([Channel.EMAIL] * em_block)
        return sequence

    @staticmethod
    def get_preferred_channel(
        cursor: int,
        sequence: Optional[Sequence[Channel]] = None,
    ) -> Tuple[Channel, int]:
        """Get preferred channel for current cursor and calculate next cursor position.

        Returns:
            Tuple of (Preferred Channel, Advanced next cursor).
        """
        seq = list(sequence or DEFAULT_ROTATION_SEQUENCE)
        if not seq:
            return Channel.WHATSAPP, cursor
        idx = cursor % len(seq)
        preferred = seq[idx]
        next_cursor = (cursor + 1) % len(seq)
        return preferred, next_cursor

    @staticmethod
    def evaluate_contact_dispatch(
        contact: Contact,
        preferred_channel: Channel,
        historical_attempts: Optional[Sequence[OutreachAttempt]] = None,
        suppressed_identifiers: Optional[Container[str]] = None,
        available_channels: Optional[Container[Channel]] = None,
    ) -> ChannelDispatchDecision:
        """Determine effective channel and exact communication endpoint for a contact.

        Rules:
        1. Fully covered contacts or suppressed/DNC contacts are not eligible.
        2. Contacts with ambiguous in-flight/recovery attempts are blocked from automatic dispatch.
        3. Preferred channel is checked first: if it has an uncovered endpoint and is available, use it.
        4. If preferred channel is unavailable or has no uncovered endpoints, safe fallback to
           the alternative channel is checked.
        5. If neither channel has an eligible uncovered endpoint, contact is marked not eligible.
        """
        # 1. Check suppression & DNC
        suppressed = suppressed_identifiers or set()
        if contact.contact_id in suppressed:
            return ChannelDispatchDecision(
                is_eligible=False,
                channel=None,
                endpoint=None,
                preferred_channel=preferred_channel,
                is_fallback=False,
                reason="CONTACT_SUPPRESSED",
            )

        if contact.tags:
            opt_out_tags = {"dnc", "opt_out", "opt-out", "do_not_contact", "unsubscribed"}
            if {t.strip().lower() for t in contact.tags} & opt_out_tags:
                return ChannelDispatchDecision(
                    is_eligible=False,
                    channel=None,
                    endpoint=None,
                    preferred_channel=preferred_channel,
                    is_fallback=False,
                    reason="CONTACT_OPTED_OUT",
                )

        # 2. Check ambiguous / in-flight blocker
        if has_ambiguous_or_inflight_blocker(contact, historical_attempts=historical_attempts):
            return ChannelDispatchDecision(
                is_eligible=False,
                channel=None,
                endpoint=None,
                preferred_channel=preferred_channel,
                is_fallback=False,
                reason="AMBIGUOUS_IN_FLIGHT_BLOCKED",
            )

        # 3. Check if contact is already fully covered
        if is_contact_fully_covered(contact, historical_attempts, suppressed):
            return ChannelDispatchDecision(
                is_eligible=False,
                channel=None,
                endpoint=None,
                preferred_channel=preferred_channel,
                is_fallback=False,
                reason="CONTACT_FULLY_COVERED",
            )

        allowed_channels = available_channels or {Channel.WHATSAPP, Channel.EMAIL}

        # 4. Try Preferred Channel
        if preferred_channel in allowed_channels:
            ep = get_next_uncovered_endpoint(
                contact=contact,
                channel=preferred_channel,
                historical_attempts=historical_attempts,
                suppressed_identifiers=suppressed,
            )
            if ep is not None:
                return ChannelDispatchDecision(
                    is_eligible=True,
                    channel=preferred_channel,
                    endpoint=ep,
                    preferred_channel=preferred_channel,
                    is_fallback=False,
                    reason=f"PREFERRED_{preferred_channel.value}_ENDPOINT_AVAILABLE",
                )

        # 5. Safe Fallback to Alternative Channel
        fallback_channel = Channel.EMAIL if preferred_channel == Channel.WHATSAPP else Channel.WHATSAPP
        if fallback_channel in allowed_channels:
            fb_ep = get_next_uncovered_endpoint(
                contact=contact,
                channel=fallback_channel,
                historical_attempts=historical_attempts,
                suppressed_identifiers=suppressed,
            )
            if fb_ep is not None:
                return ChannelDispatchDecision(
                    is_eligible=True,
                    channel=fallback_channel,
                    endpoint=fb_ep,
                    preferred_channel=preferred_channel,
                    is_fallback=True,
                    reason=f"FALLBACK_TO_{fallback_channel.value}_ENDPOINT_AVAILABLE",
                )

        # 6. Neither channel has uncovered endpoints
        return ChannelDispatchDecision(
            is_eligible=False,
            channel=None,
            endpoint=None,
            preferred_channel=preferred_channel,
            is_fallback=False,
            reason="NO_UNCOVERED_ENDPOINTS_AVAILABLE",
        )
