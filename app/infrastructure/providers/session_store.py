"""Filesystem layout and lifecycle for WhatsApp browser session profiles.

Owns path derivation, renames/merges, and cleanup for per-sender Firefox
profiles. Kept separate from auth state and browser automation.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_SESSIONS_ROOT = ROOT_DIR / ".sessions" / "whatsapp"


class SessionStore:
    """Derives and manages on-disk session directories under a root path."""

    def __init__(self, sessions_root: Optional[Path] = None) -> None:
        self.sessions_root = sessions_root or DEFAULT_SESSIONS_ROOT
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalise_id(sender_id: str) -> str:
        """Return a filesystem-safe identifier for a sender id (no mkdir)."""
        clean_id = "".join(c for c in sender_id if c.isalnum() or c in ("-", "_")).lower()
        return clean_id if clean_id else "default_sender"

    def session_dir_path(self, sender_id: str) -> Path:
        """Compute the isolated session storage path WITHOUT creating it."""
        return self.sessions_root / self.normalise_id(sender_id)

    def get_session_dir(self, sender_id: str) -> Path:
        """Return the session storage path for a sender (created if absent)."""
        s_dir = self.session_dir_path(sender_id)
        s_dir.mkdir(parents=True, exist_ok=True)
        return s_dir

    def rename_session_dir(self, from_id: str, to_id: str) -> Path:
        """Rename a session folder, retrying on Windows lock errors."""
        src = self.session_dir_path(from_id)
        dst = self.session_dir_path(to_id)
        if not src.exists():
            return dst
        if dst.exists():
            for item in src.iterdir():
                dest_item = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest_item)
            try:
                shutil.rmtree(src)
            except OSError:
                logger.warning("Failed to remove leftover temp session dir %s", src, exc_info=True)
        else:
            last_err: Optional[Exception] = None
            for attempt in range(10):
                try:
                    src.rename(dst)
                    return dst
                except OSError as exc:
                    last_err = exc
                    logger.warning("Rename retry %d for %s -> %s: %r", attempt + 1, from_id, to_id, exc)
                    time.sleep(1.0)
            raise last_err if last_err else OSError("Rename failed")
        return dst

    def remove_session_dir(self, sender_id: str) -> None:
        """Remove the on-disk session folder for an id (abandoned temp sessions)."""
        s_dir = self.session_dir_path(sender_id)
        if s_dir.exists():
            try:
                shutil.rmtree(s_dir)
            except OSError:
                logger.warning("Failed to remove session dir %s", s_dir, exc_info=True)

    @staticmethod
    def is_temp_id(sender_id: str) -> bool:
        return sender_id.startswith("tmp_auth_")

    def has_persisted_session(self, sender_id: str) -> bool:
        """True if an on-disk browser profile with cookies exists for this sender."""
        s_dir = self.session_dir_path(sender_id)
        return s_dir.is_dir() and (s_dir / "cookies.sqlite").exists()
