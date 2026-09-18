"""In-memory WhatsApp authentication state registry.

Tracks per-sender auth status, QR payloads, errors, and running auth threads.
Kept separate from filesystem layout and browser automation.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.domain.enums import SenderStatus


class AuthStateRegistry:
    """Thread-safe store of per-sender authentication state and auth threads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.states: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}

    def get(self, sender_id: str) -> Dict[str, Any]:
        """Retrieve the in-memory authentication state for a sender."""
        with self._lock:
            if sender_id not in self.states:
                self.states[sender_id] = {
                    "sender_id": sender_id,
                    "status": SenderStatus.NOT_CONFIGURED.value,
                    "qr_code": None,
                    "last_checked": None,
                    "error_message": None,
                }
            return dict(self.states[sender_id])

    def is_known(self, sender_id: str) -> bool:
        """True if this id has real, non-default in-memory auth state."""
        with self._lock:
            state = self.states.get(sender_id)
            return bool(state) and state.get("status") != SenderStatus.NOT_CONFIGURED.value

    def set(
        self,
        sender_id: str,
        status: SenderStatus,
        qr_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update and record authentication state."""
        with self._lock:
            state = {
                "sender_id": sender_id,
                "status": status.value,
                "qr_code": qr_code,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "error_message": error_message,
            }
            self.states[sender_id] = state
            return dict(state)

    def drop(self, sender_id: str) -> None:
        """Remove in-memory auth state and any tracked auth thread for an id."""
        with self._lock:
            self.states.pop(sender_id, None)
            self._threads.pop(sender_id, None)

    def has_active_thread(self, sender_id: str) -> bool:
        """True if a QR auth thread is currently running for this id."""
        with self._lock:
            thread = self._threads.get(sender_id)
            return bool(thread and thread.is_alive())

    def register_thread(self, sender_id: str, thread: threading.Thread) -> None:
        with self._lock:
            self._threads[sender_id] = thread
