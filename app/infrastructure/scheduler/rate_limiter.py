"""Rate limiting, sender availability, and pacing controller.

Enforces provider-compliant dispatch delays, sender-level concurrency isolation,
hourly/daily usage caps, and exponential error backoff without evasion mechanics.
Authoritative production pacing:
  WhatsApp = 120 seconds
  Email    = 60 seconds
"""

from __future__ import annotations

import os
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional, Tuple


class RateLimiter:
    """Thread-safe rate limiter managing sender availability and pacing."""

    def __init__(
        self,
        default_channel_delay: Optional[Dict[str, float]] = None,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 600.0,
    ) -> None:
        self._lock = threading.RLock()
        if default_channel_delay is not None:
            self.default_channel_delay = default_channel_delay
        else:
            self.default_channel_delay = {
                "WHATSAPP": float(os.environ.get("OUTREACH_CHANNEL_DELAY_WA", "120.0")),
                "EMAIL": float(os.environ.get("OUTREACH_CHANNEL_DELAY_EM", "60.0")),
            }
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

        # sender_id -> timestamp of last dispatch completion
        self._last_send_time: Dict[str, float] = {}
        # sender_id -> bool indicating active in-flight send
        self._sender_busy: Dict[str, bool] = defaultdict(bool)
        # sender_id -> consecutive failure count (for backoff)
        self._consecutive_failures: Dict[str, int] = defaultdict(int)
        # sender_id -> backoff until monotonic timestamp
        self._backoff_until: Dict[str, float] = {}
        # sender_id -> timestamps of dispatches in last 24h
        self._dispatch_history: Dict[str, Deque[float]] = defaultdict(deque)
        # sender_id -> dynamic randomized target pacing delay
        self._target_delay: Dict[str, float] = {}

    def can_send(
        self,
        sender_id: str,
        channel: str,
        daily_limit: Optional[int] = None,
        hourly_limit: Optional[int] = None,
        min_delay_override: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Check if a sender is currently eligible to dispatch a message."""
        with self._lock:
            now_mono = time.monotonic()
            now_epoch = time.time()

            # 1. Check if sender is busy with active in-flight attempt
            if self._sender_busy[sender_id]:
                return False, "SENDER_BUSY"

            # 2. Check if in backoff
            if sender_id in self._backoff_until and now_mono < self._backoff_until[sender_id]:
                wait_sec = round(self._backoff_until[sender_id] - now_mono, 1)
                return False, f"RATE_LIMITED_BACKOFF_{wait_sec}S"

            # 3. Check inter-message spacing delay with human jitter
            if min_delay_override is not None:
                min_delay = min_delay_override
            else:
                base_delay = self.default_channel_delay.get(channel.upper(), 2.0)
                if sender_id not in self._target_delay:
                    self._target_delay[sender_id] = (
                        random.uniform(base_delay * 0.8, base_delay * 1.25) if base_delay > 10 else base_delay
                    )
                min_delay = self._target_delay[sender_id]
            last_time = self._last_send_time.get(sender_id, 0.0)
            elapsed = now_mono - last_time
            if elapsed < min_delay:
                return False, f"WAITING_CHANNEL_PACE_{round(min_delay - elapsed, 2)}S"

            # 4. Check hourly and daily limits
            history = self._dispatch_history[sender_id]
            # Prune events older than 24h
            cutoff_24h = now_epoch - 86400
            while history and history[0] < cutoff_24h:
                history.popleft()

            if daily_limit and len(history) >= daily_limit:
                return False, "DAILY_LIMIT_REACHED"

            if hourly_limit:
                cutoff_1h = now_epoch - 3600
                recent_1h = sum(1 for t in history if t >= cutoff_1h)
                if recent_1h >= hourly_limit:
                    return False, "HOURLY_LIMIT_REACHED"

            return True, "READY"

    def acquire_sender(self, sender_id: str) -> bool:
        """Reserve a sender identity for exclusive use by an attempt."""
        with self._lock:
            if self._sender_busy[sender_id]:
                return False
            self._sender_busy[sender_id] = True
            return True

    def release_sender(self, sender_id: str) -> None:
        """Release a sender lock."""
        with self._lock:
            self._sender_busy[sender_id] = False

    def record_dispatch_success(self, sender_id: str) -> None:
        """Record successful dispatch completion, reset backoff, and record timestamp."""
        with self._lock:
            now_mono = time.monotonic()
            now_epoch = time.time()
            self._last_send_time[sender_id] = now_mono
            self._sender_busy[sender_id] = False
            self._consecutive_failures[sender_id] = 0
            self._backoff_until.pop(sender_id, None)
            self._target_delay.pop(sender_id, None)
            self._dispatch_history[sender_id].append(now_epoch)

    def record_dispatch_failure(self, sender_id: str, is_rate_limit: bool = False) -> float:
        """Record provider error and compute exponential backoff duration."""
        with self._lock:
            now_mono = time.monotonic()
            self._last_send_time[sender_id] = now_mono
            self._sender_busy[sender_id] = False
            self._consecutive_failures[sender_id] += 1

            count = self._consecutive_failures[sender_id]
            multiplier = 2 ** (count - 1) if count <= 5 else 32
            backoff = min(self.base_backoff_seconds * multiplier, self.max_backoff_seconds)
            if is_rate_limit:
                backoff = max(backoff, 120.0)

            self._backoff_until[sender_id] = now_mono + backoff
            return backoff

    def wait_for_ready(
        self,
        sender_id: str,
        channel: str,
        daily_limit: Optional[int] = None,
        hourly_limit: Optional[int] = None,
        timeout_seconds: float = 60.0,
        min_delay_override: Optional[float] = None,
    ) -> bool:
        """Block until sender is ready or timeout expires."""
        deadline = time.monotonic() + timeout_seconds
        min_delay = min_delay_override if min_delay_override is not None else self.default_channel_delay.get(channel.upper(), 0.01)
        sleep_step = max(0.001, min(0.05, min_delay / 2.0))
        while time.monotonic() < deadline:
            can, _ = self.can_send(sender_id, channel, daily_limit, hourly_limit, min_delay_override)
            if can:
                return True
            time.sleep(sleep_step)
        return False

    def reset_sender(self, sender_id: str) -> None:
        """Reset internal rate limiter state for a sender."""
        with self._lock:
            self._last_send_time.pop(sender_id, None)
            self._sender_busy.pop(sender_id, None)
            self._consecutive_failures.pop(sender_id, None)
            self._backoff_until.pop(sender_id, None)
            self._target_delay.pop(sender_id, None)
            self._dispatch_history.pop(sender_id, None)

    def is_sender_busy(self, sender_id: str) -> bool:
        with self._lock:
            return self._sender_busy[sender_id]


# Canonical app-wide rate limiter shared between campaign scheduler and manual sends.
default_rate_limiter = RateLimiter()
