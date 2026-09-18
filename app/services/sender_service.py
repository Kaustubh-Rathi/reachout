"""Sender Account inventory and readiness service.

Handles multi-account sender identities across channels without exposing
credentials, OAuth tokens, or session secrets. WhatsApp authentication and
email credential concerns are delegated to dedicated collaborators.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.ports.infrastructure import SessionManager
from app.services.context import ServiceContext, build_service_context
from app.services.email_sender_service import EmailSenderService
from app.services.whatsapp_auth_service import WhatsAppAuthService

logger = logging.getLogger(__name__)


class SenderService:
    """Application service managing sender inventory, readiness, and lifecycle."""

    def __init__(
        self,
        session: Session,
        session_manager: Optional[SessionManager] = None,
        context: Optional[ServiceContext] = None,
        session_factory=None,
    ) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.repo = ctx.sender_repo
        self.event_publisher = ctx.event_publisher
        self.session_manager = session_manager or ctx.session_manager
        self.credential_vault = ctx.credential_vault
        self.email_provider = ctx.email_provider
        # Background auth callbacks open their own DB session; injectable for tests.
        self._session_factory = session_factory or ctx.session_factory
        # Display names for in-memory temp WhatsApp ids (never persisted to the DB).
        self._temp_display_names: Dict[str, str] = {}
        self._whatsapp = WhatsAppAuthService(
            session=session,
            repo=self.repo,
            session_manager=self.session_manager,
            event_publisher=self.event_publisher,
            session_factory=self._session_factory,
            temp_display_names=self._temp_display_names,
        )
        self._email = EmailSenderService(
            session=session,
            repo=self.repo,
            email_provider=self.email_provider,
            event_publisher=self.event_publisher,
        )

    # ------------------------------------------------------------- WhatsApp

    def configure_whatsapp_sessions(self, count: int) -> List[Dict[str, Any]]:
        """Ensure at least N WhatsApp sessions are configured, returning the list."""
        self._whatsapp.configure_whatsapp_sessions(count)
        return self.list_senders(channel="WHATSAPP")

    def add_whatsapp_session(
        self,
        display_name: Optional[str] = None,
        identity: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._whatsapp.add_whatsapp_session(display_name=display_name, identity=identity)

    def start_whatsapp_authentication(self, sender_id: str) -> Dict[str, Any]:
        return self._whatsapp.start_whatsapp_authentication(sender_id)

    def get_whatsapp_auth_status(self, sender_id: str) -> Dict[str, Any]:
        return self._whatsapp.get_whatsapp_auth_status(sender_id)

    def check_whatsapp_session_health(self, sender_id: str, timeout_seconds: int = 20) -> Dict[str, Any]:
        return self._whatsapp.check_whatsapp_session_health(sender_id, timeout_seconds=timeout_seconds)

    # ---------------------------------------------------------------- Email

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
        return self._email.configure_email_sender(
            id=id,
            identity=identity,
            display_name=display_name,
            host=host,
            port=port,
            user=user,
            password=password,
            verify_now=verify_now,
        )

    def verify_email_sender(self, sender_id: str) -> Dict[str, Any]:
        return self._email.verify_email_sender(sender_id)

    # ---------------------------------------------------------- reconciliation

    def reconcile_sender_states(self) -> None:
        """Reconcile stored sender statuses against persistent auth contexts and vaults on startup."""
        wa_senders = self.repo.list_by_channel(Channel.WHATSAPP)
        for s in wa_senders:
            # A persisted ACTIVE WhatsApp sender whose on-disk authenticated profile
            # is missing must not keep passing the readiness gate.
            if s.status == SenderStatus.ACTIVE and not self.session_manager.has_persisted_session(s.id):
                s.status = SenderStatus.AUTH_REQUIRED
                self.repo.save(s)
                continue
            # Only reconcile when we hold real in-memory auth state; a default
            # NOT_CONFIGURED after restart must not overwrite persisted ACTIVE.
            if not self.session_manager.is_auth_known(s.id):
                continue
            auth_info = self.session_manager.get_auth_state(s.id)
            st_str = auth_info.get("status")
            if st_str and st_str != s.status.value:
                try:
                    s.status = SenderStatus(st_str)
                    self.repo.save(s)
                except ValueError as exc:
                    logger.warning("Skipping unrecognized auth status %r for sender %s: %s", st_str, s.id, exc)

        em_senders = self.repo.list_by_channel(Channel.EMAIL)

        # Materialize vault-only email credentials into sender_accounts rows.
        existing_em_ids = {s.id for s in em_senders}
        existing_em_identities = {(s.identity or "").strip().casefold() for s in em_senders}
        for vid in self.credential_vault.list_senders_with_credentials():
            if vid not in existing_em_ids:
                creds = self.credential_vault.get_credentials(vid) or {}
                user = (creds.get("user") or "").strip()
                candidate_identity = user or vid
                normalized_identity = candidate_identity.strip().casefold()
                if normalized_identity in existing_em_identities:
                    logger.warning("Skipping vault sender %r: duplicate EMAIL identity already registered", vid)
                    continue
                new_sender = SenderAccount(
                    id=vid,
                    channel=Channel.EMAIL,
                    provider="smtp",
                    identity=candidate_identity,
                    display_name=candidate_identity,
                    status=SenderStatus.ACTIVE if user and creds.get("password") else SenderStatus.AUTH_REQUIRED,
                    daily_limit=100,
                    hourly_limit=20,
                )
                try:
                    with self.session.begin_nested():
                        self.repo.save(new_sender)
                except IntegrityError:
                    logger.warning("Skipping vault sender %r: duplicate EMAIL identity already registered", vid)
                    continue
                existing_em_ids.add(vid)
                existing_em_identities.add(normalized_identity)

        em_senders = self.repo.list_by_channel(Channel.EMAIL)
        for s in em_senders:
            creds = self.email_provider.get_sender_credentials(s.id)
            has_creds = bool(creds.get("user") and creds.get("password"))
            if s.status == SenderStatus.ACTIVE and not has_creds:
                s.status = SenderStatus.AUTH_REQUIRED
                self.repo.save(s)

        self.session.commit()

    # ------------------------------------------------------------- inventory

    def list_senders(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all sender accounts in safe projection (no secrets/tokens/credentials)."""
        ch_enum = Channel(channel.upper()) if channel else None
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
            "SENDER_STATUS_CHANGED",
            {
                "sender_id": sender.id,
                "channel": sender.channel.value,
                "status": sender.status.value,
                "display_name": sender.display_name,
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

    def deactivate_sender(self, sender_id: str) -> Optional[Dict[str, Any]]:
        """Deactivate a sender so it exits future rotation without deleting history."""
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
        """Mark a sender INACTIVE (preserves attempt FK integrity)."""
        return self.deactivate_sender(sender_id) is not None

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
