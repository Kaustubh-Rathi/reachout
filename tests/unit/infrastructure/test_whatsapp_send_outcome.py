"""Unit tests for the WhatsApp send-outcome check.

A click only queues a message; WhatsApp then paints a tick (accepted) or a red
marker (rejected). The provider must read that verdict instead of assuming the
click succeeded, otherwise a rejected message is recorded as SENT.
"""

from __future__ import annotations

import time

from app.infrastructure.providers.playwright_whatsapp_provider import await_send_outcome


class _Page:
    """Stand-in for a Playwright page: returns a scripted verdict each call."""

    def __init__(self, verdicts: list[str] | str) -> None:
        self._verdicts = [verdicts] if isinstance(verdicts, str) else list(verdicts)
        self.script: str | None = None
        self.calls = 0

    def evaluate(self, script: str) -> str:
        self.script = script
        self.calls += 1
        if len(self._verdicts) > 1:
            return self._verdicts.pop(0)
        return self._verdicts[0] if self._verdicts else "pending"


class TestAwaitSendOutcome:
    def test_rejected_send_reports_a_reason(self):
        reason = await_send_outcome(_Page("failed"), within_seconds=2.0)
        assert reason is not None
        assert "not sent" in reason

    def test_acknowledged_send_returns_none_quickly(self):
        page = _Page("ack")
        started = time.monotonic()
        assert await_send_outcome(page, within_seconds=10.0) is None
        assert time.monotonic() - started < 2.0, "an acknowledged send must not wait out the deadline"
        assert page.calls == 1

    def test_verdict_is_read_until_it_settles(self):
        # Bubble renders as pending, then flips to failed.
        page = _Page(["pending", "pending", "failed"])
        reason = await_send_outcome(page, within_seconds=5.0)
        assert reason is not None
        assert page.calls >= 3

    def test_no_verdict_keeps_historical_behaviour(self):
        assert await_send_outcome(_Page("pending"), within_seconds=0.2) is None

    def test_script_only_inspects_the_newest_row(self):
        page = _Page("pending")
        await_send_outcome(page, within_seconds=0.2)
        assert page.script is not None
        # An older failed bubble must not poison later sends.
        assert 'rows[rows.length - 1]' in page.script

    def test_evaluate_errors_do_not_break_the_send(self):
        class FlakyPage:
            def evaluate(self, script):
                raise RuntimeError("DOM detached mid-render")

        assert await_send_outcome(FlakyPage(), within_seconds=0.2) is None
