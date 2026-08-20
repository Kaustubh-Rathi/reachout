"""WhatsApp Multi-Session Playwright Manager.

Manages N isolated browser contexts under .sessions/whatsapp/<sender_id>/ to support
multiple independent WhatsApp sender accounts concurrently without collision.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.domain.enums import SenderStatus

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_SESSIONS_ROOT = ROOT_DIR / ".sessions" / "whatsapp"


class WhatsAppSessionManager:
    """Manages persistent browser directories and contexts for N WhatsApp senders."""

    def __init__(self, sessions_root: Optional[Path] = None) -> None:
        self.sessions_root = sessions_root or DEFAULT_SESSIONS_ROOT
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._auth_state: Dict[str, Dict[str, Any]] = {}
        self._active_auth_threads: Dict[str, threading.Thread] = {}

    def get_session_dir(self, sender_id: str) -> Path:
        """Return the isolated session storage path for a given sender ID."""
        clean_id = "".join(c for c in sender_id if c.isalnum() or c in ("-", "_")).lower()
        if not clean_id:
            clean_id = "default_sender"
        s_dir = self.sessions_root / clean_id
        s_dir.mkdir(parents=True, exist_ok=True)
        return s_dir

    def get_auth_state(self, sender_id: str) -> Dict[str, Any]:
        """Retrieve the in-memory authentication state for a sender."""
        with self._lock:
            if sender_id not in self._auth_state:
                self._auth_state[sender_id] = {
                    "sender_id": sender_id,
                    "status": SenderStatus.NOT_CONFIGURED.value,
                    "qr_code": None,
                    "last_checked": None,
                    "error_message": None,
                }
            return dict(self._auth_state[sender_id])

    def set_auth_state(
        self,
        sender_id: str,
        status: SenderStatus,
        qr_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update and record authentication state."""
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            state = {
                "sender_id": sender_id,
                "status": status.value,
                "qr_code": qr_code,
                "last_checked": now_iso,
                "error_message": error_message,
            }
            self._auth_state[sender_id] = state
            return dict(state)

    def check_session_status(self, sender_id: str, timeout_seconds: int = 15) -> SenderStatus:
        """Probe session health to determine if authenticated, auth required, or expired."""
        try:
            from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]
        except ImportError:
            return SenderStatus.INACTIVE

        session_dir = self.get_session_dir(sender_id)
        try:
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    str(session_dir),
                    headless=True,
                    viewport={"width": 1280, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

                # Check if logged in (chat list or search input visible)
                main_ui = page.locator('#side, [data-testid="chat-list"], [aria-label="Search input textbox"]')
                # Check if QR code canvas is visible
                qr_canvas = page.locator('canvas[aria-label="Scan this QR code to link a device"], div[data-ref]')

                try:
                    main_ui.first.wait_for(state="visible", timeout=timeout_seconds * 1000)
                    context.close()
                    self.set_auth_state(sender_id, SenderStatus.ACTIVE)
                    return SenderStatus.ACTIVE
                except Exception:
                    if qr_canvas.count() > 0 and qr_canvas.first.is_visible():
                        context.close()
                        self.set_auth_state(sender_id, SenderStatus.QR_REQUIRED)
                        return SenderStatus.QR_REQUIRED
                    context.close()
                    self.set_auth_state(sender_id, SenderStatus.AUTH_REQUIRED)
                    return SenderStatus.AUTH_REQUIRED
        except Exception as exc:
            self.set_auth_state(sender_id, SenderStatus.ERROR, error_message=str(exc))
            return SenderStatus.DISCONNECTED

    def start_qr_authentication(
        self,
        sender_id: str,
        on_event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Initiate background QR authentication process for a WhatsApp sender account."""
        with self._lock:
            existing_thread = self._active_auth_threads.get(sender_id)
            if existing_thread and existing_thread.is_alive():
                return self.get_auth_state(sender_id)

            self.set_auth_state(sender_id, SenderStatus.AUTHENTICATING)
            if on_event_callback:
                on_event_callback(
                    "sender.auth_progress",
                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "AUTHENTICATING", "message": "Launching authentication browser..."},
                )

            t = threading.Thread(
                target=self._run_auth_flow,
                args=(sender_id, on_event_callback, timeout_seconds),
                daemon=True,
                name=f"wa-auth-{sender_id}",
            )
            self._active_auth_threads[sender_id] = t
            t.start()

            return self.get_auth_state(sender_id)

    def _run_auth_flow(
        self,
        sender_id: str,
        callback: Optional[Callable[[str, Dict[str, Any]], None]],
        timeout_seconds: int = 120,
    ) -> None:
        """Background worker driving Playwright QR code capture and login detection."""
        try:
            from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]
        except ImportError:
            self.set_auth_state(
                sender_id, SenderStatus.ERROR, error_message="Playwright is not installed in the environment."
            )
            if callback:
                callback(
                    "sender.status_changed",
                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "ERROR", "error": "Playwright missing"},
                )
            return

        session_dir = self.get_session_dir(sender_id)
        try:
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    str(session_dir),
                    headless=True,
                    viewport={"width": 1280, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=45000)

                main_ui = page.locator('#side, [data-testid="chat-list"], [aria-label="Search input textbox"]')
                qr_canvas = page.locator('canvas[aria-label="Scan this QR code to link a device"], div[data-ref]')

                deadline = time.monotonic() + timeout_seconds
                qr_emitted = False

                while time.monotonic() < deadline:
                    # Check if already authenticated
                    if main_ui.count() > 0 and main_ui.first.is_visible():
                        self.set_auth_state(sender_id, SenderStatus.ACTIVE)
                        context.close()
                        if callback:
                            callback(
                                "sender.status_changed",
                                {"sender_id": sender_id, "channel": "WHATSAPP", "status": "ACTIVE"},
                            )
                        return

                    # Check for QR canvas
                    if not qr_emitted and qr_canvas.count() > 0 and qr_canvas.first.is_visible():
                        try:
                            qr_bytes = qr_canvas.first.screenshot()
                            qr_b64 = base64.b64encode(qr_bytes).decode("utf-8")
                            qr_data_url = f"data:image/png;base64,{qr_b64}"
                            self.set_auth_state(sender_id, SenderStatus.QR_REQUIRED, qr_code=qr_data_url)
                            qr_emitted = True
                            if callback:
                                callback(
                                    "sender.qr_received",
                                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "QR_REQUIRED", "qr_code": qr_data_url},
                                )
                                callback(
                                    "sender.status_changed",
                                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "QR_REQUIRED"},
                                )
                        except Exception:
                            pass

                    time.sleep(1.0)

                # Deadline expired without login
                context.close()
                self.set_auth_state(
                    sender_id, SenderStatus.AUTH_REQUIRED, error_message="QR scan timed out without authentication."
                )
                if callback:
                    callback(
                        "sender.status_changed",
                        {"sender_id": sender_id, "channel": "WHATSAPP", "status": "AUTH_REQUIRED", "error": "Timeout"},
                    )

        except Exception as exc:
            self.set_auth_state(sender_id, SenderStatus.ERROR, error_message=str(exc))
            if callback:
                callback(
                    "sender.status_changed",
                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "ERROR", "error": str(exc)},
                )
