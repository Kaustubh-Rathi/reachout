"""WhatsApp sender session provisioning and QR authentication.

Owns the WhatsApp-specific lifecycle (session counts, temp sessions, QR auth
callbacks, health probing) so the sender inventory service stays focused.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.domain.enums import Channel, SenderStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.sender_account import SenderAccount
from app.ports.infrastructure import EventPublisher, SessionManager
from app.ports.repositories import SenderRepository

logger = logging.getLogger(__name__)


def normalise_full_phone(phone: str) -> str:
    """Return the phone as its bare digit string (used as the sender identity)."""
    return re.sub(r"\D", "", phone or "")


class WhatsAppAuthService:
    """Manages WhatsApp sender session configuration and authentication flows."""

    def __init__(
        self,
        session: Session,
        repo: SenderRepository,
        session_manager: SessionManager,
        event_publisher: EventPublisher,
        session_factory: Callable[..., Any],
        temp_display_names: Dict[str, str],
    ) -> None:
        self.session = session
        self.repo = repo
        self.session_manager = session_manager
        self.event_publisher = event_publisher
        self._session_factory = session_factory
        self._temp_display_names = temp_display_names

    def configure_whatsapp_sessions(self, count: int) -> None:
        """Ensure at least N WhatsApp sessions exist in the repository."""
        if count < 1:
            raise ValidationError("WhatsApp session count must be at least 1")
        existing_map = {s.id: s for s in self.repo.list_by_channel(Channel.WHATSAPP)}

        for i in range(1, count + 1):
            sess_id = f"WA_SESSION_{i}"
            if sess_id not in existing_map:
                new_sender = SenderAccount(
                    id=sess_id,
                    channel=Channel.WHATSAPP,
                    provider="playwright_whatsapp",
                    identity="",
                    display_name=f"WhatsApp Session {i}",
                    status=SenderStatus.AUTH_REQUIRED,
                    daily_limit=50,
                    hourly_limit=10,
                )
                self.repo.save(new_sender)
                self.session_manager.set_auth_state(sess_id, SenderStatus.AUTH_REQUIRED)

        self.session.commit()

    def add_whatsapp_session(
        self,
        display_name: Optional[str] = None,
        identity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Begin a brand-new WhatsApp session purely in memory (not persisted)."""
        temp_id = f"tmp_auth_{uuid.uuid4().hex[:12]}"
        name = display_name.strip() if display_name else "Pending WhatsApp Session"
        self._temp_display_names[temp_id] = name

        self.session_manager.set_auth_state(temp_id, SenderStatus.AUTH_REQUIRED)
        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": temp_id,
                "channel": Channel.WHATSAPP.value,
                "status": SenderStatus.AUTH_REQUIRED.value,
                "display_name": name,
            },
        )

        return {
            "id": temp_id,
            "channel": Channel.WHATSAPP.value,
            "provider": "playwright_whatsapp",
            "identity": "",
            "display_name": name,
            "status": SenderStatus.AUTH_REQUIRED.value,
            "daily_limit": 50,
            "hourly_limit": 10,
        }

    def start_whatsapp_authentication(self, sender_id: str) -> Dict[str, Any]:
        """Trigger QR authentication for a WhatsApp session (temp or re-auth)."""
        is_temp = sender_id.startswith("tmp_auth_")

        existing_phone: Optional[str] = None
        temp_display_name = "WhatsApp Session"
        if not is_temp:
            sender = self.repo.get_by_id(sender_id)
            if not sender or sender.channel != Channel.WHATSAPP:
                raise NotFoundError(f"WhatsApp sender '{sender_id}' not found")
            sender.status = SenderStatus.AUTHENTICATING
            self.repo.save(sender)
            self.session.commit()
            existing_phone = sender.identity or None
        else:
            temp_display_name = self._temp_display_names.get(sender_id, "WhatsApp Session")

        def _resolve(sid: str, phone: str) -> Dict[str, Any]:
            if existing_phone is not None:
                if self._normalise_phone(phone) != self._normalise_phone(existing_phone):
                    return {
                        "status": "reject",
                        "error": "Phone number mismatch: re-authentication must use the same WhatsApp number.",
                    }
                return {"status": "reuse"}

            final_id = self._whatsapp_sender_id(phone)
            if self._phone_exists(final_id):
                return {
                    "status": "reject",
                    "error": "Duplicate phone number: a WhatsApp session for this number already exists.",
                }
            return {"status": "persist", "final_id": final_id}

        def _auth_callback(event_name: str, payload: Dict[str, Any]) -> None:
            st_val = payload.get("status")
            if st_val == "ACTIVE" and payload.get("temp_id"):
                try:
                    self._persist_whatsapp_session(payload, temp_display_name)
                except Exception as exc:
                    logger.exception("Failed to persist WhatsApp session for %s", sender_id)
                    self.session_manager.set_auth_state(
                        sender_id,
                        SenderStatus.ERROR,
                        error_message=f"Failed to persist WhatsApp session: {exc}",
                    )
            elif st_val:
                try:
                    with self._session_factory() as cb_sess:
                        from app.composition import build_repositories

                        cb_repo = build_repositories(cb_sess).sender
                        cb_sender = cb_repo.get_by_id(payload.get("sender_id") or sender_id)
                        if cb_sender:
                            cb_sender.status = SenderStatus(st_val)
                            cb_repo.save(cb_sender)
                            cb_sess.commit()
                except Exception:
                    logger.exception("Re-auth status sync failed for sender %s", sender_id)

            self.event_publisher.publish_event(event_name, payload)

        auth_state = self.session_manager.start_qr_authentication(
            sender_id=sender_id,
            on_event_callback=_auth_callback,
            is_temp=is_temp,
            on_resolve=_resolve,
        )

        display_name = temp_display_name
        if not is_temp:
            cb_sender = self.repo.get_by_id(sender_id)
            display_name = cb_sender.display_name if cb_sender else temp_display_name

        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": sender_id,
                "channel": Channel.WHATSAPP.value,
                "status": auth_state["status"],
                "display_name": display_name,
            },
        )
        return auth_state

    def get_whatsapp_auth_status(self, sender_id: str) -> Dict[str, Any]:
        """Get live authentication status and QR code if required."""
        sender = self.repo.get_by_id(sender_id)
        is_temp = self.session_manager.is_auth_known(sender_id)

        if not sender and not is_temp:
            raise NotFoundError(f"Sender '{sender_id}' not found")

        auth_state = self.session_manager.get_auth_state(sender_id)
        return {
            "sender_id": sender_id,
            "status": auth_state.get("status") or (sender.status.value if sender else None),
            "channel": Channel.WHATSAPP.value,
            "display_name": sender.display_name if sender else self._temp_display_names.get(sender_id, sender_id),
            "identity": sender.identity if sender else "",
            "qr_code": auth_state.get("qr_code"),
            "last_checked": auth_state.get("last_checked"),
            "error_message": auth_state.get("error_message"),
        }

    def check_whatsapp_session_health(self, sender_id: str, timeout_seconds: int = 20) -> Dict[str, Any]:
        """Probe a persisted WhatsApp profile and sync the stored status."""
        sender = self.repo.get_by_id(sender_id)
        if not sender or sender.channel != Channel.WHATSAPP:
            raise NotFoundError(f"WhatsApp sender '{sender_id}' not found")

        probe = self.session_manager.check_session_status(sender_id, timeout_seconds=timeout_seconds)
        previous = sender.status
        if probe == SenderStatus.ACTIVE:
            sender.status = SenderStatus.ACTIVE
        elif probe in (SenderStatus.QR_REQUIRED, SenderStatus.AUTH_REQUIRED):
            sender.status = probe
        if sender.status != previous:
            self.repo.save(sender)
            self.session.commit()
            self.event_publisher.publish_event(
                "SENDER_STATUS_CHANGED",
                {"sender_id": sender.id, "channel": Channel.WHATSAPP.value, "status": sender.status.value},
            )
        return {
            "sender_id": sender.id,
            "channel": Channel.WHATSAPP.value,
            "probe": probe.value,
            "status": sender.status.value,
            "status_changed": sender.status != previous,
        }

    @staticmethod
    def _normalise_phone(phone: Optional[str]) -> str:
        return re.sub(r"\D", "", phone or "")

    def _whatsapp_sender_id(self, phone: str) -> str:
        digits = self._normalise_phone(phone)
        if not digits:
            raise ValidationError("Cannot derive sender id: extracted phone number is empty.")
        return f"wa_{digits}"

    def _phone_exists(self, final_id: str) -> bool:
        return self.repo.get_by_id(final_id) is not None

    def _persist_whatsapp_session(self, payload: Dict[str, Any], display_name: str) -> None:
        """Create the real SenderAccount for a freshly-authenticated session."""
        final_id = payload.get("sender_id")
        phone = payload.get("phone")
        temp_id = payload.get("temp_id")
        if not final_id or not phone:
            raise ValidationError("Cannot persist WhatsApp session: missing final id or phone.")

        with self._session_factory() as db_sess:
            from app.composition import build_repositories

            repo = build_repositories(db_sess).sender
            new_sender = SenderAccount(
                id=final_id,
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity=normalise_full_phone(phone),
                display_name=display_name,
                status=SenderStatus.ACTIVE,
                daily_limit=50,
                hourly_limit=10,
            )
            repo.save(new_sender)
            db_sess.commit()
            db_sess.expire_all()

        if temp_id:
            self._temp_display_names.pop(temp_id, None)

        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {"sender_id": final_id, "channel": "WHATSAPP", "status": "ACTIVE", "display_name": display_name},
        )
