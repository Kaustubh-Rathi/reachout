"""WhatsApp Multi-Session Camoufox Manager.

Manages N isolated browser contexts under .sessions/whatsapp/<sender_id>/ to support
multiple independent WhatsApp sender accounts concurrently without collision.

Lifecycle (matching the no-fake-row refactor):
- A *new* session uses an in-memory temporary UUID (``tmp_auth_<uuid>``) that physically
  lives at ``.sessions/whatsapp/tmp_auth_<uuid>``. It is NEVER written to the database.
- On successful login, the worker extracts the phone number and asks an ``on_resolve``
  callback (supplied by the service layer) for the decision:
    * ``persist``  -> the temp folder is renamed to the stable ``wa_<phone>`` id while the
      browser context is still open, so the QR-linked profile moves intact.
    * ``reuse``    -> re-authentication of an existing sender: same id/folder, no rename.
    * ``reject``   -> duplicate phone / phone mismatch; the temp folder is discarded and
      an error is surfaced.
- Abandoned temporary sessions (QR timeout) have their temp folder removed so a later
  poll correctly 404s instead of silently failing.
"""

from __future__ import annotations

import base64
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.domain.enums import SenderStatus

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_SESSIONS_ROOT = ROOT_DIR / ".sessions" / "whatsapp"
LOG_PATH = ROOT_DIR / "logs" / "whatsapp_auth.log"


def _auth_log(message: str) -> None:
    """Append a timestamped line to logs/whatsapp_auth.log for diagnosing QR auth."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {message}\n")
    except Exception:
        pass


class WhatsAppSessionManager:
    """Manages persistent browser directories and contexts for N WhatsApp senders."""

    def __init__(self, sessions_root: Optional[Path] = None) -> None:
        self.sessions_root = sessions_root or DEFAULT_SESSIONS_ROOT
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._auth_state: Dict[str, Dict[str, Any]] = {}
        self._active_auth_threads: Dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------ paths

    def _normalise_id(self, sender_id: str) -> str:
        """Return a filesystem-safe identifier for a sender id (no mkdir)."""
        clean_id = "".join(c for c in sender_id if c.isalnum() or c in ("-", "_")).lower()
        return clean_id if clean_id else "default_sender"

    def session_dir_path(self, sender_id: str) -> Path:
        """Compute the isolated session storage path WITHOUT creating it.

        Used by the rename/cleanup logic when the exact on-disk location matters
        and a spurious mkdir would interfere with renames.
        """
        return self.sessions_root / self._normalise_id(sender_id)

    def get_session_dir(self, sender_id: str) -> Path:
        """Return the isolated session storage path for a given sender ID (created if absent)."""
        s_dir = self.session_dir_path(sender_id)
        s_dir.mkdir(parents=True, exist_ok=True)
        return s_dir

    def rename_session_dir(self, from_id: str, to_id: str) -> Path:
        """Rename a session folder from one id to another, returning the target path.

        Retries on Windows lock errors because the Firefox profile may still be briefly
        held open right after the browser context closes.
        """
        src = self.session_dir_path(from_id)
        dst = self.session_dir_path(to_id)
        if not src.exists():
            # Nothing on disk yet (e.g. brand-new temp that never wrote a profile).
            return dst
        if dst.exists():
            # Target already present and src differs: merge by copying contents over.
            for item in src.iterdir():
                dest_item = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest_item)
            try:
                shutil.rmtree(src)
            except Exception as exc:
                # Surface a leftover temp dir instead of silently ignoring it.
                _auth_log(f"[auth] WARN: failed to remove leftover temp session dir {src}: {exc!r}")
        else:
            last_err: Optional[Exception] = None
            for attempt in range(10):
                try:
                    src.rename(dst)
                    return dst
                except OSError as exc:
                    last_err = exc
                    _auth_log(f"[auth] rename retry {attempt + 1} for {from_id} -> {to_id}: {exc!r}")
                    time.sleep(1.0)
            raise last_err if last_err else OSError("Rename failed")
        return dst

    def remove_session_dir(self, sender_id: str) -> None:
        """Remove the on-disk session folder for an id (used for abandoned temp sessions)."""
        s_dir = self.session_dir_path(sender_id)
        if s_dir.exists():
            try:
                shutil.rmtree(s_dir)
            except Exception as exc:
                # Surface the leftover dir instead of silently ignoring it.
                _auth_log(f"[auth] WARN: failed to remove session dir {s_dir}: {exc!r}")

    def is_temp_id(self, sender_id: str) -> bool:
        return sender_id.startswith("tmp_auth_")

    def has_persisted_session(self, sender_id: str) -> bool:
        """True if an on-disk browser profile with cookies exists for this sender.

        Used at startup to avoid trusting a stale ACTIVE status for a WhatsApp
        sender whose authenticated profile is gone.
        """
        s_dir = self.session_dir_path(sender_id)
        return s_dir.is_dir() and (s_dir / "cookies.sqlite").exists()

    # ----------------------------------------------------------- auth state

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

    def is_auth_known(self, sender_id: str) -> bool:
        """True if this id has real, non-default in-memory auth state (e.g. a live temp)."""
        with self._lock:
            state = self._auth_state.get(sender_id)
            return bool(state) and state.get("status") != SenderStatus.NOT_CONFIGURED.value

    def has_active_auth_thread(self, sender_id: str) -> bool:
        """True if a QR auth thread is currently running for this id."""
        with self._lock:
            t = self._active_auth_threads.get(sender_id)
            return bool(t and t.is_alive())

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

    def drop_auth_state(self, sender_id: str) -> None:
        """Remove in-memory auth state and any tracked auth thread for an id.

        After this, ``get_auth_state`` reports the default NOT_CONFIGURED and a caller
        that keeps a strict ``is known`` check can treat the id as unknown -> 404.
        """
        with self._lock:
            self._auth_state.pop(sender_id, None)
            self._active_auth_threads.pop(sender_id, None)

    # ------------------------------------------------------------- probing

    def check_session_status(self, sender_id: str, timeout_seconds: int = 15) -> SenderStatus:
        """Probe session health to determine if authenticated, auth required, or expired."""
        try:
            from camoufox.sync_api import Camoufox
        except ImportError:
            return SenderStatus.INACTIVE

        session_dir = self.get_session_dir(sender_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        # Overwrite (not append): append mode accumulated duplicate prefs.
        with open(session_dir / "user.js", "w") as f:
            f.write('user_pref("privacy.trackingprotection.enabled", false);\n')
            f.write('user_pref("privacy.trackingprotection.pbmode.enabled", false);\n')
            f.write('user_pref("privacy.partition.network_state", false);\n')
            f.write('user_pref("media.peerconnection.enabled", true);\n')
            f.write('user_pref("permissions.default.image", 1);\n')

        try:
            with Camoufox(
                persistent_context=True,
                user_data_dir=str(session_dir),
                headless=False,
                humanize=True,
                os="windows",
                window=(1280, 900),
            ) as context:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

                main_ui = page.locator('#side, [data-testid="chat-list"], [aria-label="Search input textbox"]')
                qr_canvas = page.locator(
                    'canvas[aria-label="Scan this QR code to link a device"], div[data-ref], canvas'
                )

                try:
                    main_ui.first.wait_for(state="visible", timeout=timeout_seconds * 1000)
                    self.set_auth_state(sender_id, SenderStatus.ACTIVE)
                    return SenderStatus.ACTIVE
                except Exception:
                    if qr_canvas.count() > 0 and qr_canvas.first.is_visible():
                        self.set_auth_state(sender_id, SenderStatus.QR_REQUIRED)
                        return SenderStatus.QR_REQUIRED
                    self.set_auth_state(sender_id, SenderStatus.AUTH_REQUIRED)
                    return SenderStatus.AUTH_REQUIRED
        except Exception as exc:
            self.set_auth_state(sender_id, SenderStatus.ERROR, error_message=str(exc))
            return SenderStatus.DISCONNECTED

    # --------------------------------------------------------- auth lifecycle

    def start_qr_authentication(
        self,
        sender_id: str,
        on_event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        timeout_seconds: int = 120,
        is_temp: bool = False,
        on_resolve: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Initiate background QR authentication process for a WhatsApp sender account.

        Parameters
        ----------
        sender_id : str
            The id this auth flow is running under. For a brand-new session this is a
            temporary ``tmp_auth_<uuid>``; for re-authentication it is the ``wa_<phone>``.
        is_temp : bool
            True when ``sender_id`` is a throwaway temp uuid. Temp ids are removed from
            disk and in-memory state on failure/timeout; real ids are preserved.
        on_resolve : Callable[[str, str], dict] | None
            Invoked inside the worker once the phone number is extracted. Receives
            ``(sender_id, phone)`` and returns a decision dict:
              * ``{"status": "persist", "final_id": "wa_<phone>"}``
              * ``{"status": "reuse"}``
              * ``{"status": "reject", "error": "<reason>"}``
        """
        with self._lock:
            existing_thread = self._active_auth_threads.get(sender_id)
            if existing_thread and existing_thread.is_alive():
                return self.get_auth_state(sender_id)

            self.set_auth_state(sender_id, SenderStatus.AUTHENTICATING)
            if on_event_callback:
                on_event_callback(
                    "sender.auth_progress",
                    {
                        "sender_id": sender_id,
                        "channel": "WHATSAPP",
                        "status": "AUTHENTICATING",
                        "message": "Launching authentication browser...",
                    },
                )

            t = threading.Thread(
                target=self._run_auth_flow,
                args=(sender_id, on_event_callback, timeout_seconds, is_temp, on_resolve),
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
        is_temp: bool = False,
        on_resolve: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ) -> None:
        """Background worker driving Camoufox QR code capture and login detection."""
        try:
            from camoufox.sync_api import Camoufox
        except ImportError:
            self._fail_auth(
                sender_id, callback, is_temp, "Camoufox is not installed in the environment.", "Camoufox missing"
            )
            return

        session_dir = self.get_session_dir(sender_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        # Overwrite (not append): append mode accumulated duplicate prefs.
        with open(session_dir / "user.js", "w") as f:
            f.write('user_pref("privacy.trackingprotection.enabled", false);\n')
            f.write('user_pref("privacy.trackingprotection.pbmode.enabled", false);\n')
            f.write('user_pref("privacy.partition.network_state", false);\n')
            f.write('user_pref("media.peerconnection.enabled", true);\n')
            f.write('user_pref("permissions.default.image", 1);\n')

        pending_persist = None  # (final_id, phone) set when we must persist after browser closes
        try:
            with Camoufox(
                persistent_context=True,
                user_data_dir=str(session_dir),
                headless=False,
                humanize=False,  # speed up QR: no human-like delays during auth
                os="windows",
                window=(1280, 900),
            ) as context:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=45000)

                main_ui = page.locator('#side, [data-testid="chat-list"], [aria-label="Search input textbox"]')
                qr_canvas = page.locator(
                    'canvas[aria-label="Scan this QR code to link a device"], div[data-ref], canvas'
                )

                deadline = time.monotonic() + timeout_seconds
                qr_emitted = False

                while time.monotonic() < deadline:
                    # Check if already authenticated
                    if main_ui.count() > 0 and main_ui.first.is_visible():
                        _auth_log(f"[auth] sender={sender_id} LOGIN_DETECTED main_ui_visible=True")
                        phone = self._extract_phone(page)
                        _auth_log(f"[auth] sender={sender_id} extracted_phone={phone!r}")
                        if phone is None:
                            self._fail_auth(
                                sender_id,
                                callback,
                                is_temp,
                                "Could not extract phone number from WhatsApp Web.",
                                "PHONE_EXTRACTION_FAILED",
                            )
                            return

                        # Ask the service layer how to persist this session.
                        if on_resolve:
                            decision = on_resolve(sender_id, phone)
                            status = decision.get("status")
                            _auth_log(f"[auth] sender={sender_id} on_resolve_decision={decision}")

                            if status == "persist":
                                final_id = decision.get("final_id") or sender_id
                                # Do NOT rename yet: Firefox is still holding the profile dir
                                # open, and Windows cannot rename a directory in use (WinError 5).
                                # Break out so the with-block closes the browser, then rename below.
                                pending_persist = (final_id, phone)
                                break

                            if status == "reuse":
                                # Re-authentication of an existing sender: same id/folder.
                                self.set_auth_state(sender_id, SenderStatus.ACTIVE)
                                _auth_log(f"[auth] sender={sender_id} REUSED phone={phone}")
                                if callback:
                                    callback(
                                        "SENDER_STATUS_CHANGED",
                                        {
                                            "sender_id": sender_id,
                                            "channel": "WHATSAPP",
                                            "status": "ACTIVE",
                                            "phone": phone,
                                        },
                                    )
                                return

                            # reject: duplicate phone / phone mismatch / other.
                            reason = str(decision.get("error") or "Authentication rejected.")
                            self._fail_auth(sender_id, callback, is_temp, reason, "REJECTED")
                            return

                        # No resolver: treat as plain success (used by probes).
                        self.set_auth_state(sender_id, SenderStatus.ACTIVE)
                        if callback:
                            callback(
                                "SENDER_STATUS_CHANGED",
                                {"sender_id": sender_id, "channel": "WHATSAPP", "status": "ACTIVE", "phone": phone},
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
                            _auth_log(f"[auth] sender={sender_id} QR_CAPTURED len={len(qr_data_url)}")
                            if callback:
                                callback(
                                    "sender.qr_received",
                                    {
                                        "sender_id": sender_id,
                                        "channel": "WHATSAPP",
                                        "status": "QR_REQUIRED",
                                        "qr_code": qr_data_url,
                                    },
                                )
                                callback(
                                    "SENDER_STATUS_CHANGED",
                                    {"sender_id": sender_id, "channel": "WHATSAPP", "status": "QR_REQUIRED"},
                                )
                        except Exception:
                            pass

                    time.sleep(1.0)

                # Deadline expired without login (only if we did NOT break for persist)
                if pending_persist is None:
                    timeout_msg = "QR scan timed out without authentication."
                    _auth_log(f"[auth] sender={sender_id} TIMEOUT after {timeout_seconds}s (no login detected)")
                    self.set_auth_state(sender_id, SenderStatus.AUTH_REQUIRED, error_message=timeout_msg)
                    if callback:
                        callback(
                            "SENDER_STATUS_CHANGED",
                            {
                                "sender_id": sender_id,
                                "channel": "WHATSAPP",
                                "status": "AUTH_REQUIRED",
                                "error": "Timeout",
                            },
                        )
                    # Abandoned temporary sessions leave no trace.
                    if is_temp:
                        self.remove_session_dir(sender_id)
                        self.drop_auth_state(sender_id)

        except Exception as exc:
            _auth_log(f"[auth] sender={sender_id} UNEXPECTED_ERROR {exc!r}")
            self._fail_auth(sender_id, callback, is_temp, str(exc), "UNEXPECTED_ERROR")

        # The browser context is now closed, so the profile dir is unlocked.
        if pending_persist is not None:
            final_id, phone = pending_persist
            if final_id != sender_id:
                self.rename_session_dir(sender_id, final_id)
            self.set_auth_state(final_id, SenderStatus.ACTIVE)
            self.set_auth_state(sender_id, SenderStatus.ACTIVE)
            _auth_log(f"[auth] sender={sender_id} PERSISTED final_id={final_id} phone={phone}")
            if callback:
                callback(
                    "SENDER_STATUS_CHANGED",
                    {
                        "sender_id": final_id,
                        "channel": "WHATSAPP",
                        "status": "ACTIVE",
                        "phone": phone,
                        "temp_id": sender_id,
                    },
                )

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _extract_phone(page: Any) -> Optional[str]:
        """Extract the WhatsApp phone number from localStorage immediately at login."""
        try:
            wid = page.evaluate('window.localStorage.getItem("last-wid-md")')
            if wid and ":" in wid:
                return wid.split(":")[0].replace('"', "").replace("'", "")
            return None
        except Exception as exc:
            print(f"[WhatsAppSessionManager] Error extracting phone number: {exc}")
            return None

    def _fail_auth(
        self,
        sender_id: str,
        callback: Optional[Callable[[str, Dict[str, Any]], None]],
        is_temp: bool,
        message: str,
        code: str,
    ) -> None:
        """Mark auth as errored and tear down ephemeral temp sessions."""
        _auth_log(f"[auth] FAIL sender={sender_id} code={code} message={message}")
        self.set_auth_state(sender_id, SenderStatus.ERROR, error_message=message)
        if callback:
            callback(
                "SENDER_STATUS_CHANGED",
                {"sender_id": sender_id, "channel": "WHATSAPP", "status": "ERROR", "error": code, "message": message},
            )
        if is_temp:
            self.remove_session_dir(sender_id)
            self.drop_auth_state(sender_id)


# Canonical app-wide singleton so QR auth state survives across HTTP requests.
default_session_manager = WhatsAppSessionManager()
