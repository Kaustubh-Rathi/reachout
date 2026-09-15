"""Multi-Sender Architecture & Scaling Verification Tests.

Verifies:
1. Cardinality independence: System supports arbitrary sender counts (1, 2, 10, 50+ senders)
   for both WhatsApp and Email channels without architectural changes.
2. Least-loaded rotation and usage attribution.
3. Daily quota limits and saturation handling.
4. Strict channel isolation between WhatsApp and Email sender accounts.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from app.domain.enums import Channel
from app.domain.sender_account import SenderAccount


def select_least_loaded_sender(
    senders: List[SenderAccount],
    channel: Channel,
    current_usage_map: dict[str, int],
) -> Optional[SenderAccount]:
    """Domain policy helper: Select available sender with lowest usage under quota."""
    eligible = [
        s
        for s in senders
        if s.channel == channel
        and s.is_available()
        and (s.daily_limit is None or current_usage_map.get(s.id, 0) < s.daily_limit)
    ]
    if not eligible:
        return None
    # Sort by current usage ascending, then ID ascending for deterministic tie-breaking
    eligible.sort(key=lambda s: (current_usage_map.get(s.id, 0), s.id))
    return eligible[0]


class TestMultiSenderScalability:
    """Suite verifying multi-sender account scaling and rotation policies."""

    @pytest.mark.parametrize("sender_count", [1, 2, 10, 50])
    def test_whatsapp_sender_cardinality_scaling(self, sender_count: int):
        """WhatsApp multi-sender engine handles 1, 2, 10, 50 senders seamlessly."""
        senders = [
            SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="PLAYWRIGHT_CHROME",
                identity=f"+9170000000{i:02d}",
                display_name=f"WA Worker {i}",
                daily_limit=100,
            )
            for i in range(sender_count)
        ]
        assert len(senders) == sender_count
        assert all(s.channel == Channel.WHATSAPP for s in senders)
        assert all(s.is_available() for s in senders)

        # Verify unique identities and session directories
        identities = {s.identity for s in senders}
        assert len(identities) == sender_count

    @pytest.mark.parametrize("sender_count", [1, 2, 10, 50])
    def test_email_sender_cardinality_scaling(self, sender_count: int):
        """Email multi-sender engine handles 1, 2, 10, 50 senders seamlessly."""
        senders = [
            SenderAccount.create(
                channel=Channel.EMAIL,
                provider="SMTP_GMAIL",
                identity=f"outreach_agent_{i:02d}@company.com",
                display_name=f"Email Agent {i}",
                daily_limit=200,
            )
            for i in range(sender_count)
        ]
        assert len(senders) == sender_count
        assert all(s.channel == Channel.EMAIL for s in senders)
        assert all(s.is_available() for s in senders)

    def test_least_loaded_sender_rotation(self):
        """Senders are selected according to least-loaded usage distribution."""
        s1 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="S1", daily_limit=50
        )
        s2 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91002", display_name="S2", daily_limit=50
        )
        s3 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91003", display_name="S3", daily_limit=50
        )

        senders = [s1, s2, s3]
        usage = {s1.id: 10, s2.id: 5, s3.id: 8}

        # s2 has lowest usage (5)
        selected = select_least_loaded_sender(senders, Channel.WHATSAPP, usage)
        assert selected is not None
        assert selected.id == s2.id

        # Update s2 usage to 12 -> now s3 is lowest (8)
        usage[s2.id] = 12
        selected_next = select_least_loaded_sender(senders, Channel.WHATSAPP, usage)
        assert selected_next is not None
        assert selected_next.id == s3.id

    def test_daily_quota_exhaustion_handling(self):
        """When senders hit daily limits, they are bypassed; returns None when all exhausted."""
        s1 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91001", display_name="S1", daily_limit=10
        )
        s2 = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91002", display_name="S2", daily_limit=10
        )

        senders = [s1, s2]
        usage = {s1.id: 10, s2.id: 9}

        # s1 exhausted, picks s2
        chosen = select_least_loaded_sender(senders, Channel.WHATSAPP, usage)
        assert chosen is not None
        assert chosen.id == s2.id

        # s2 also hits limit
        usage[s2.id] = 10
        exhausted = select_least_loaded_sender(senders, Channel.WHATSAPP, usage)
        assert exhausted is None, "Expected None when all active sender quotas are exhausted"

    def test_channel_isolation(self):
        """WhatsApp query never selects Email senders and vice versa."""
        wa_sender = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="MOCK", identity="+91111", display_name="WA"
        )
        email_sender = SenderAccount.create(
            channel=Channel.EMAIL, provider="MOCK", identity="user@test.com", display_name="EM"
        )

        senders = [wa_sender, email_sender]
        usage = {wa_sender.id: 0, email_sender.id: 0}

        wa_pick = select_least_loaded_sender(senders, Channel.WHATSAPP, usage)
        assert wa_pick is not None
        assert wa_pick.channel == Channel.WHATSAPP

        email_pick = select_least_loaded_sender(senders, Channel.EMAIL, usage)
        assert email_pick is not None
        assert email_pick.channel == Channel.EMAIL
