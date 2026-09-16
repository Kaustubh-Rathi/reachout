"""Sender Account and Channel Rotation Policy.

Selects HOW / THROUGH WHICH SENDER:
- Supports arbitrary N active sender accounts without hardcoded limits.
- Builds dynamic deterministic rotation sequences across configured active senders.
- Enforces sender availability precedence (skips unavailable or auth-required senders
  to select the next eligible sender without interrupting campaign execution).
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

from app.domain.sender_account import SenderAccount


class SenderRotationPolicy:
    """Policy responsible for dynamic sender and channel rotation."""

    @staticmethod
    def select_next_sender(
        senders: Sequence[SenderAccount],
        cursor: int,
        availability_checker: Optional[Callable[[SenderAccount], bool]] = None,
    ) -> Tuple[Optional[SenderAccount], int]:
        """Select next eligible sender account starting from cursor, skipping unavailable ones.

        Args:
            senders: List of candidate senders.
            cursor: Current rotation cursor offset.
            availability_checker: Optional predicate checking additional dynamic rate limits/sessions.

        Returns:
            Tuple of (Selected SenderAccount or None, Updated cursor index).
        """
        if not senders:
            return None, cursor

        total = len(senders)
        for offset in range(total):
            idx = (cursor + offset) % total
            candidate = senders[idx]

            # 1. Base status availability check
            if not candidate.is_available():
                continue

            # 2. Dynamic availability check (e.g. rate limiter, auth, or session)
            if availability_checker is not None:
                if not availability_checker(candidate):
                    continue

            # Candidate is available and eligible!
            next_cursor = (idx + 1) % total
            return candidate, next_cursor

        # All senders are currently unavailable
        return None, cursor
