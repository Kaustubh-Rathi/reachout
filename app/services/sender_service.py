"""Sender Account management service.

Handles multi-account sender identities across channels without exposing credentials,
OAuth tokens, or session secrets.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.providers.factory import (
    get_email_provider,
)
from app.infrastructure.providers.session_manager import (
    WhatsAppSessionManager,
    default_session_manager,
)
from app.services.context import ServiceContext, build_service_context


def _normalise_full_phone(phone: str) -> str:
    """Return the phone as its bare digit string (used as the sender identity)."""
    return re.sub(r"\D", "", phone or "")


class SenderService:
    """Application service managing multi-sender accounts, authentication, and readiness."""

    def __init__(
        self,
        session: Session,
        session_manager: Optional[WhatsAppSessionManager] = None,
        context: Optional[ServiceContext] = None,
        session_factory=None,
    ) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.repo = ctx.sender_repo
        self.event_publisher = ctx.event_publisher
        self.session_manager = session_manager or default_session_manager
        # Background auth callbacks open their own DB session; injectable for tests.
        self._session_factory = session_factory or SessionFactory
        # Display names for in-memory temp WhatsApp ids (never persisted to the DB).
        self._temp_display_names: Dict[str, str] = {}

    def reconcile_sender_states(self) -> None:
        """Reconcile stored sender statuses against persistent auth contexts and vaults on startup."""
        # WhatsApp senders reconciliation
        wa_senders = self.repo.list_by_channel(Channel.WHATSAPP)
        for s in wa_senders:
            # A persisted ACTIVE WhatsApp sender whose on-disk authenticated profile
            # is missing must not keep passing the readiness gate. Downgrade it so the
            # operator is prompted to re-authenticate (symmetric with email creds).
            if s.status == SenderStatus.ACTIVE and not self.session_manager.has_persisted_session(s.id):
                s.status = SenderStatus.AUTH_REQUIRED
                self.repo.save(s)
                continue
            # Only reconcile when we actually hold a real, non-default in-memory auth
            # state (a live temp flow or an in-progress re-auth). After a process
            # restart the in-memory auth dict is empty, and get_auth_state() returns a
            # NOT_CONFIGURED default that must NOT overwrite a persisted ACTIVE status
            # (otherwise authenticated WhatsApp sessions silently regress to
            # NOT_CONFIGURED on every boot and vanish from the dashboard).
            if not self.session_manager.is_auth_known(s.id):
                continue
            auth_info = self.session_manager.get_auth_state(s.id)
            st_str = auth_info.get("status")
            if st_str and st_str != s.status.value:
                try:
                    s.status = SenderStatus(st_str)
                    self.repo.save(s)
                except ValueError as exc:
                    # An unrecognized status string in the session manager is a real
                    # divergence; surface it instead of silently skipping it.
                    print(f"[SenderService] Skipping unrecognized auth status {st_str!r} for sender {s.id}: {exc}")

        # Email senders reconciliation
        em_senders = self.repo.list_by_channel(Channel.EMAIL)

        # F3/B4: Materialize vault-only email credentials into sender_accounts rows so no
        # stored credential is stranded as a "phantom session" without a DB record.
        from app.infrastructure.security.credential_vault import default_credential_vault

        existing_em_ids = {s.id for s in em_senders}
        vault_ids = default_credential_vault.list_senders_with_credentials()
        for vid in vault_ids:
            if vid not in existing_em_ids:
                creds = default_credential_vault.get_credentials(vid) or {}
                user = (creds.get("user") or "").strip()
                new_sender = SenderAccount(
                    id=vid,
                    channel=Channel.EMAIL,
                    provider="smtp",
                    identity=user or vid,
                    display_name=user or vid,
                    status=SenderStatus.ACTIVE if user and creds.get("password") else SenderStatus.AUTH_REQUIRED,
                    daily_limit=100,
                    hourly_limit=20,
                )
                self.repo.save(new_sender)

        # Refresh after materialization so the loop below sees newly-created rows.
        em_senders = self.repo.list_by_channel(Channel.EMAIL)
        email_provider = get_email_provider()
        for s in em_senders:
            has_creds = False
            if hasattr(email_provider, "get_sender_credentials"):
                c = email_provider.get_sender_credentials(s.id)
                has_creds = bool(c.get("user") and c.get("password"))

            if s.status == SenderStatus.ACTIVE and not has_creds:
                s.status = SenderStatus.AUTH_REQUIRED
                self.repo.save(s)

        self.session.commit()

    def list_senders(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all sender accounts in safe projection (no secrets/tokens/credentials)."""
        ch_enum = Channel(channel.upper()) if channel else None

        senders = []
        if ch_enum:
            senders = self.repo.list_by_channel(ch_enum)
        else:
            senders = self.repo.list_by_channel(Channel.WHATSAPP) + self.repo.list_by_channel(Channel.EMAIL)

        results = []
        for s in senders:
            auth_info = self.session_manager.get_auth_state(s.id) if s.channel == Channel.WHATSAPP else {}
            results.append(
                {
                    "id": s.id,
                    "channel": s.channel.value,
                    "provider": s.provider,
                    "identity": s.identity,
                    "display_name": s.display_name,
                    "status": s.status.value,
                    "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None,
                    "daily_limit": s.daily_limit,
                    "hourly_limit": s.hourly_limit,
                    "qr_code": auth_info.get("qr_code"),
                    "error_message": auth_info.get("error_message"),
                    "last_checked": auth_info.get("last_checked"),
                }
            )
        return results

    def get_sender(self, sender_id: str) -> Optional[SenderAccount]:
        return self.repo.get_by_id(sender_id)

    def update_sender_status(self, sender_id: str, status_str: str) -> Optional[Dict[str, Any]]:
        """Update sender status and broadcast change."""
        sender = self.repo.get_by_id(sender_id)
        if not sender:
            return None

        status_enum = SenderStatus(status_str.upper())
        sender.status = status_enum
        self.repo.save(sender)
        self.session.commit()

        if sender.channel == Channel.WHATSAPP:
            self.session_manager.set_auth_state(sender_id, status_enum)

        self.event_publisher.publish_event(
            "sender.status_changed",
            {
                "sender_id": sender.id,
                "channel": sender.channel.value,
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )
        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": sender.id,
                "channel": sender.channel.value,
                "status": sender.status.value,
            },
        )

        return {
            "id": sender.id,
            "channel": sender.channel.value,
            "provider": sender.provider,
            "identity": sender.identity,
            "display_name": sender.display_name,
            "status": sender.status.value,
        }

    def configure_whatsapp_sessions(self, count: int) -> List[Dict[str, Any]]:
        """Ensure exactly N WhatsApp sessions are configured in the repository."""
        if count < 1:
            raise ValueError("WhatsApp session count must be at least 1")
        existing_wa = self.repo.list_by_channel(Channel.WHATSAPP)
        existing_map = {s.id: s for s in existing_wa}

        # Create or update sessions up to count
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
        return self.list_senders(channel="WHATSAPP")

    def start_whatsapp_authentication(self, sender_id: str) -> Dict[str, Any]:
        """Trigger QR authentication for a WhatsApp session.

        Two modes:
        * temp id (``tmp_auth_<uuid>``): brand-new session. Never persisted until login
          succeeds; the worker renames the temp folder to ``wa_<phone>`` and this service
          materialises the real record only on success.
        * existing ``wa_<phone>``: re-authentication. Reuses the same folder/id and guards
          that the scanned number still matches the stored one.
        """
        is_temp = sender_id.startswith("tmp_auth_")

        existing_phone: Optional[str] = None
        temp_display_name = "WhatsApp Session"
        if not is_temp:
            sender = self.repo.get_by_id(sender_id)
            if not sender or sender.channel != Channel.WHATSAPP:
                raise ValueError(f"WhatsApp sender '{sender_id}' not found")
            sender.status = SenderStatus.AUTHENTICATING
            self.repo.save(sender)
            self.session.commit()
            existing_phone = sender.identity or None
        else:
            # Display name for the pending temp session (set at add time).
            temp_display_name = self._temp_display_names.get(sender_id, "WhatsApp Session")

        def _resolve(sid: str, phone: str) -> Dict[str, Any]:
            """Decide how to persist a successfully-logged-in WhatsApp session.

            Runs in the background worker's thread; must not touch the caller's HTTP-bound
            session object, so it opens its own DB session via SessionFactory.
            """
            if existing_phone is not None:
                # Re-authentication of a stored sender: number must match.
                if self._normalise_phone(phone) != self._normalise_phone(existing_phone):
                    return {
                        "status": "reject",
                        "error": "Phone number mismatch: re-authentication must use the same WhatsApp number.",
                    }
                return {"status": "reuse"}

            # Brand-new temp session: persist only if the phone is unique.
            final_id = self._whatsapp_sender_id(phone)
            if self._phone_exists(final_id):
                return {
                    "status": "reject",
                    "error": "Duplicate phone number: a WhatsApp session for this number already exists.",
                }
            return {"status": "persist", "final_id": final_id}

        def _auth_callback(event_name: str, payload: Dict[str, Any]) -> None:
            st_val = payload.get("status")
            # Materialise the real DB record for a freshly-authenticated temp session.
            if st_val == "ACTIVE" and payload.get("temp_id"):
                try:
                    self._persist_whatsapp_session(payload, temp_display_name)
                except Exception as exc:
                    # Surface the failure instead of leaving a phantom ACTIVE session
                    # with no DB row (the operator would be misled into thinking the
                    # WhatsApp session persisted). Mark auth state ERROR so the UI shows it.
                    import traceback

                    traceback.print_exc()
                    self.session_manager.set_auth_state(
                        sender_id,
                        SenderStatus.ERROR,
                        error_message=f"Failed to persist WhatsApp session: {exc}",
                    )
            elif st_val:
                # Sync status for an existing persisted sender (re-auth).
                try:
                    with self._session_factory() as cb_sess:
                        cb_repo = build_service_context(cb_sess).sender_repo
                        cb_sender = cb_repo.get_by_id(payload.get("sender_id") or sender_id)
                        if cb_sender:
                            cb_sender.status = SenderStatus(st_val)
                            cb_repo.save(cb_sender)
                            cb_sess.commit()
                except Exception:
                    # Surface re-auth status-sync failures so sender status never silently
                    # diverges from reality.
                    import traceback

                    traceback.print_exc()

            self.event_publisher.publish_event(event_name, payload)
            alias_name = event_name.upper().replace(".", "_")
            self.event_publisher.publish_event(alias_name, payload)

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
            "sender.status_changed",
            {
                "sender_id": sender_id,
                "channel": Channel.WHATSAPP.value,
                "status": auth_state["status"],
                "display_name": display_name,
            },
        )

        return auth_state

    def get_whatsapp_auth_status(self, sender_id: str) -> Dict[str, Any]:
        """Get live authentication status and QR code if required.

        Supports both live temp ids (brand-new, in-memory only) and persisted ``wa_<phone>``
        senders. Raises ``ValueError`` (mapped to HTTP 404) for ids that are neither a known
        temp auth flow nor a stored sender.
        """
        sender = self.repo.get_by_id(sender_id)
        is_temp = self.session_manager.is_auth_known(sender_id)

        if not sender and not is_temp:
            raise ValueError(f"Sender '{sender_id}' not found")

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

    def configure_email_sender(
        self,
        id: str,
        identity: str,
        display_name: str,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        verify_now: bool = True,
    ) -> Dict[str, Any]:
        """Create/register an Email sender and verify its SMTP connection in one step.

        This is the single entry point for Email sessions (``/api/senders/email/add`` has
        been removed). If SMTP verification fails, an exception is raised and NOTHING is
        persisted ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no dummy/placeholder row is ever created.
        """

        clean_id = id.strip()
        clean_identity = identity.strip()
        clean_display = display_name.strip()
        clean_user = (user or clean_identity).strip()

        if not clean_identity:
            raise ValueError("Email address (identity) is required.")

        # Auto-index the sender id (EMAIL_SESSION_N) when not supplied.
        if not clean_id:
            clean_id = self._next_email_session_id()

        provider = get_email_provider()

        # Verify credentials BEFORE persisting anything.
        if verify_now and clean_user and password:
            if hasattr(provider, "set_sender_credentials"):
                provider.set_sender_credentials(
                    sender_account_id=clean_id,
                    user=clean_user,
                    password=password or "",
                    host=host,
                    port=port,
                )
            if hasattr(provider, "verify_credentials"):
                success, err = provider.verify_credentials(clean_id)
                if not success:
                    raise ValueError(f"SMTP connection failed: {err or 'could not connect'}")
            # No dedicated verifier: fall back to a documented connection check.
            elif not host:
                raise ValueError("SMTP host is required to verify the connection.")
        else:
            if not clean_user or not password:
                raise ValueError("SMTP username and password are required.")
            if verify_now and not host:
                raise ValueError("SMTP host is required to verify the connection.")

        # Persist only after verification succeeded.
        sender = self.repo.get_by_id(clean_id)
        if not sender:
            sender = SenderAccount(
                id=clean_id,
                channel=Channel.EMAIL,
                provider="smtp",
                identity=clean_identity,
                display_name=clean_display,
                status=SenderStatus.ACTIVE,
                daily_limit=100,
                hourly_limit=20,
            )
        else:
            sender.identity = clean_identity
            sender.display_name = clean_display
            sender.status = SenderStatus.ACTIVE

        if hasattr(provider, "set_sender_credentials"):
            provider.set_sender_credentials(
                sender_account_id=clean_id,
                user=clean_user,
                password=password or "",
                host=host,
                port=port,
            )

        self.repo.save(sender)
        self.session.commit()

        self.event_publisher.publish_event(
            "sender.status_changed",
            {
                "sender_id": sender.id,
                "channel": "EMAIL",
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )

        return {
            "id": sender.id,
            "channel": "EMAIL",
            "identity": sender.identity,
            "display_name": sender.display_name,
            "status": sender.status.value,
            "verified": True,
            "error_message": None,
        }

    def _next_email_session_id(self) -> str:
        """Return the next available ``EMAIL_SESSION_N`` id."""
        existing = self.repo.list_by_channel(Channel.EMAIL)
        max_idx = 0
        for s in existing:
            m = re.match(r"(?:EMAIL_)?session_(\d+)", s.id, flags=re.IGNORECASE)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        return f"EMAIL_SESSION_{max_idx + 1}"

    def verify_email_sender(self, sender_id: str) -> Dict[str, Any]:
        """Verify SMTP credentials for an existing email sender account."""
        sender = self.repo.get_by_id(sender_id)
        if not sender or sender.channel != Channel.EMAIL:
            raise ValueError(f"Email sender '{sender_id}' not found")

        provider = get_email_provider()
        if hasattr(provider, "verify_credentials"):
            success, err = provider.verify_credentials(sender_id)
            if success:
                sender.status = SenderStatus.ACTIVE
                verification_error = None
            else:
                sender.status = SenderStatus.ERROR
                verification_error = err
        else:
            sender.status = SenderStatus.ACTIVE
            verification_error = None

        self.repo.save(sender)
        self.session.commit()

        self.event_publisher.publish_event(
            "sender.status_changed",
            {
                "sender_id": sender.id,
                "channel": "EMAIL",
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )

        return {
            "id": sender.id,
            "channel": "EMAIL",
            "status": sender.status.value,
            "verified": sender.status == SenderStatus.ACTIVE,
            "error_message": verification_error,
        }

    def get_senders_readiness(self) -> Dict[str, Any]:
        """Retrieve overall sender readiness metrics across channels."""
        wa_senders = self.repo.list_by_channel(Channel.WHATSAPP)
        em_senders = self.repo.list_by_channel(Channel.EMAIL)

        active_wa = [s for s in wa_senders if s.status == SenderStatus.ACTIVE]
        active_em = [s for s in em_senders if s.status == SenderStatus.ACTIVE]

        return {
            "whatsapp": {
                "active_count": len(active_wa),
                "total_count": len(wa_senders),
                "ready": len(active_wa) > 0,
                "senders": [
                    {"id": s.id, "display_name": s.display_name, "status": s.status.value, "identity": s.identity}
                    for s in wa_senders
                ],
            },
            "email": {
                "active_count": len(active_em),
                "total_count": len(em_senders),
                "ready": len(active_em) > 0,
                "senders": [
                    {"id": s.id, "display_name": s.display_name, "status": s.status.value, "identity": s.identity}
                    for s in em_senders
                ],
            },
            "overall_ready": len(active_wa) > 0 or len(active_em) > 0,
        }

    def add_whatsapp_session(
        self,
        display_name: Optional[str] = None,
        identity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Begin a brand-new WhatsApp session purely in memory.

        Returns a throwaway ``tmp_auth_<uuid>`` that is NOT persisted to the database.
        The real ``wa_<phone>`` record is created only once the user scans the QR and the
        worker confirms a unique phone number (see ``start_whatsapp_authentication``).
        """
        import uuid

        temp_id = f"tmp_auth_{uuid.uuid4().hex[:12]}"
        name = display_name.strip() if display_name else "Pending WhatsApp Session"
        self._temp_display_names[temp_id] = name

        self.session_manager.set_auth_state(temp_id, SenderStatus.AUTH_REQUIRED)

        event_payload = {
            "sender_id": temp_id,
            "channel": Channel.WHATSAPP.value,
            "status": SenderStatus.AUTH_REQUIRED.value,
            "display_name": name,
        }
        self.event_publisher.publish_event("sender.status_changed", event_payload)
        self.event_publisher.publish_event("SENDER_STATUS_CHANGED", event_payload)

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

    # ------------------------------------------------------------------ whatsapp helpers

    @staticmethod
    def _normalise_phone(phone: Optional[str]) -> str:
        """Normalise a phone number to its bare digits for comparison/id building."""
        return re.sub(r"\D", "", phone or "")

    def _whatsapp_sender_id(self, phone: str) -> str:
        """Build the stable sender id for a WhatsApp phone number (``wa_<digits>``)."""
        digits = self._normalise_phone(phone)
        if not digits:
            raise ValueError("Cannot derive sender id: extracted phone number is empty.")
        return f"wa_{digits}"

    def _phone_exists(self, final_id: str) -> bool:
        """True if a WhatsApp sender with this id already exists in the database."""
        existing = self.repo.get_by_id(final_id)
        return existing is not None

    def _persist_whatsapp_session(self, payload: Dict[str, Any], display_name: str) -> None:
        """Create the complete, real SenderAccount for a freshly-authenticated session.

        The temp folder has already been renamed to ``wa_<phone>`` by the worker by the
        time this is called. Opens its own DB session so it is safe in the background
        worker thread.
        """
        final_id = payload.get("sender_id")
        phone = payload.get("phone")
        temp_id = payload.get("temp_id")
        if not final_id or not phone:
            raise ValueError("Cannot persist WhatsApp session: missing final id or phone.")

        with self._session_factory() as db_sess:
            repo = build_service_context(db_sess).sender_repo
            new_sender = SenderAccount(
                id=final_id,
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity=_normalise_full_phone(phone),
                display_name=display_name,
                status=SenderStatus.ACTIVE,
                daily_limit=50,
                hourly_limit=10,
            )
            repo.save(new_sender)
            db_sess.commit()
            db_sess.expire_all()

        # Keep the temp display-name/no bookkeeping clean.
        if temp_id:
            self._temp_display_names.pop(temp_id, None)

        self.event_publisher.publish_event(
            "sender.status_changed",
            {"sender_id": final_id, "channel": "WHATSAPP", "status": "ACTIVE", "display_name": display_name},
        )
        self.event_publisher.publish_event(
            "SENDER_STATUS_CHANGED", {"sender_id": final_id, "channel": "WHATSAPP", "status": "ACTIVE"}
        )

    def deactivate_sender(self, sender_id: str) -> Optional[Dict[str, Any]]:
        """Deactivate a sender account so it exits future rotation without deleting history."""
        return self.update_sender_status(sender_id, SenderStatus.INACTIVE.value)

    def reactivate_sender(
        self,
        sender_id: str,
        status: SenderStatus = SenderStatus.ACTIVE,
    ) -> Optional[Dict[str, Any]]:
        """Reactivate a deactivated sender account."""
        sender = self.repo.get_by_id(sender_id)
        if not sender:
            return None
        return self.update_sender_status(sender_id, status.value)

    def remove_sender(self, sender_id: str) -> bool:
        """Controlled removal of sender from active routing (marks INACTIVE to preserve attempt FK integrity)."""
        res = self.deactivate_sender(sender_id)
        return res is not None

    def create_sender(
        self,
        id: str,
        channel: Channel,
        provider: str,
        identity: str,
        display_name: str,
        status: SenderStatus = SenderStatus.ACTIVE,
        daily_limit: Optional[int] = None,
        hourly_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        sender = SenderAccount(
            id=id.strip(),
            channel=channel,
            provider=provider.strip(),
            identity=identity.strip(),
            display_name=display_name.strip(),
            status=status,
            daily_limit=daily_limit,
            hourly_limit=hourly_limit,
        )
        self.repo.save(sender)
        self.session.commit()
        return {
            "id": sender.id,
            "channel": sender.channel.value,
            "provider": sender.provider,
            "identity": sender.identity,
            "display_name": sender.display_name,
            "status": sender.status.value,
        }
