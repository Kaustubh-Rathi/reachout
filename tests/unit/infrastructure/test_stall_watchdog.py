"""Unit tests for the campaign stall watchdog predicate."""

from app.infrastructure.scheduler.campaign_scheduler import (
    STALL_NO_PROGRESS_PAUSE_SECONDS,
    should_pause_on_stall,
)


class TestShouldPauseOnStall:
    def test_no_remaining_work_never_pauses(self):
        assert (
            should_pause_on_stall(
                last_progress_mono=0.0,
                now_mono=10_000.0,
                has_uncovered_remaining=False,
            )
            is False
        )

    def test_recent_progress_does_not_pause(self):
        assert (
            should_pause_on_stall(
                last_progress_mono=1000.0,
                now_mono=1000.0 + 60.0,
                has_uncovered_remaining=True,
            )
            is False
        )

    def test_stale_progress_with_remaining_work_pauses(self):
        assert (
            should_pause_on_stall(
                last_progress_mono=0.0,
                now_mono=float(STALL_NO_PROGRESS_PAUSE_SECONDS),
                has_uncovered_remaining=True,
            )
            is True
        )

    def test_custom_threshold_is_respected(self):
        assert (
            should_pause_on_stall(
                last_progress_mono=0.0,
                now_mono=59.0,
                has_uncovered_remaining=True,
                threshold_seconds=60.0,
            )
            is False
        )
        assert (
            should_pause_on_stall(
                last_progress_mono=0.0,
                now_mono=60.0,
                has_uncovered_remaining=True,
                threshold_seconds=60.0,
            )
            is True
        )

    def test_default_threshold_is_thirty_minutes(self):
        assert STALL_NO_PROGRESS_PAUSE_SECONDS == 30 * 60
