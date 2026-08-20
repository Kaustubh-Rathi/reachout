"""Sender Account management service.

Handles multi-account sender identities across channels without exposing credentials,
OAuth tokens, or session secrets.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.providers.factory import (
    get_email_provider,
    get_whatsapp_provider,
)
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.services.event_bus import event_bus


def get_default_senders() -> List[SenderAccount]:
    is_live = os.environ.get("OUTREACH_MODE", "mock").strip().lower() == "live"
    init_status = SenderStatus.AUTH_REQUIRED if is_live else SenderStatus.ACTIVE
    return [
        SenderAccount(
            id="WA_SESSION_1",
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+91 98765 00001",
            display_name="WhatsApp Session 1",
            status=init_status,
            daily_limit=50,
            hourly_limit=10,
        ),
        SenderAccount(
            id="WA_SESSION_2",
            channel=Channel.WHATSAPP,
            provider="playwright_whatsapp",
            identity="+91 98765 00002",
            display_name="WhatsApp Session 2",
            status=init_status,
            daily_limit=50,
            hourly_limit=10,
        ),
        SenderAccount(
            id="EMAIL_SESSION_1",
            channel=Channel.EMAIL,
            provider="smtp",
            identity="outreach.primary@domain.com",
            display_name="Email Session 1 (Primary)",
            status=init_status,
            daily_limit=100,
            hourly_limit=20,
        ),
    ]


DEFAULT_SENDERS = get_default_senders()


class SenderService:
    """Application service managing multi-sender accounts, authentication, and readiness."""

    def __init__(
        self,
        session: Session,
        session_manager: Optional[WhatsAppSessionManager] = None,
    ) -> None:
        self.session = session
        self.repo = SqliteSenderRepository(session)
        self.session_manager = session_manager or WhatsAppSessionManager()

    def seed_defaults_if_empty(self) -> None:
        """Seed default sender accounts if repository is empty."""
        active = self.repo.list_active()
        wa = self.repo.list_by_channel(Channel.WHATSAPP)
        em = self.repo.list_by_channel(Channel.EMAIL)
        if not active and not wa and not em:
            for s in get_default_senders():
                self.repo.save(s)
            self.session.commit()

    def reconcile_sender_states(self) -> None:
        """Reconcile stored sender statuses against persistent auth contexts and vaults on startup."""
        self.seed_defaults_if_empty()
        is_live = os.environ.get("OUTREACH_MODE", "mock").strip().lower() == "live"

        # WhatsApp senders reconciliation
        wa_senders = self.repo.list_by_channel(Channel.WHATSAPP)
        for s in wa_senders:
            if is_live:
                live_status = self.session_manager.check_session_status(s.id)
                if s.status != live_status:
                    s.status = live_status
                    self.repo.save(s)
            else:
                auth_info = self.session_manager.get_auth_state(s.id)
                st_str = auth_info.get("status")
                if st_str and st_str != s.status.value:
                    try:
                        s.status = SenderStatus(st_str)
                        self.repo.save(s)
                    except ValueError:
                        pass

        # Email senders reconciliation
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
        self.seed_defaults_if_empty()
        ch_enum = Channel(channel.upper()) if channel else None
        
        senders = []
        if ch_enum:
            senders = self.repo.list_by_channel(ch_enum)
        else:
            senders = self.repo.list_by_channel(Channel.WHATSAPP) + self.repo.list_by_channel(Channel.EMAIL)

        results = []
        for s in senders:
            auth_info = self.session_manager.get_auth_state(s.id) if s.channel == Channel.WHATSAPP else {}
            results.append({
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
            })
        return results

    def get_sender(self, sender_id: str) -> Optional[SenderAccount]:
        self.seed_defaults_if_empty()
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

        event_bus.publish_event(
            "sender.status_changed",
            {
                "sender_id": sender.id,
                "channel": sender.channel.value,
                "status": sender.status.value,
                "display_name": sender.display_name,
            },
        )
        event_bus.publish_event(
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

        self.seed_defaults_if_empty()
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
                    identity=f"+91 98765 {i:05d}",
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
        """Trigger QR authentication for a WhatsApp session."""
        sender = self.repo.get_by_id(sender_id)
        if not sender or sender.channel != Channel.WHATSAPP:
            raise ValueError(f"WhatsApp sender '{sender_id}' not found")

        # Update status to AUTHENTICATING
        sender.status = SenderStatus.AUTHENTICATING
        self.repo.save(sender)
        self.session.commit()

        def _auth_callback(event_name: str, payload: Dict[str, Any]) -> None:
            # Sync back to DB if status changed
            st_val = payload.get("status")
            if st_val:
                try:
                    with SessionFactory() as cb_sess:
                        cb_repo = SqliteSenderRepository(cb_sess)
                        cb_sender = cb_repo.get_by_id(sender_id)
                        if cb_sender:
                            cb_sender.status = SenderStatus(st_val)
                            cb_repo.save(cb_sender)
                            cb_sess.commit()
                except Exception as exc:
                    print(f"[SenderService] Error syncing auth status: {exc}")

            event_bus.publish_event(event_name, payload)
            # Also publish legacy/uppercase alias
            alias_name = event_name.upper().replace(".", "_")
            event_bus.publish_event(alias_name, payload)

        auth_state = self.session_manager.start_qr_authentication(
            sender_id=sender_id,
            on_event_callback=_auth_callback,
        )

        event_bus.publish_event(
            "sender.status_changed",
            {
                "sender_id": sender.id,
                "channel": sender.channel.value,
                "status": auth_state["status"],
                "display_name": sender.display_name,
            },
        )

        return auth_state

    def get_whatsapp_auth_status(self, sender_id: str) -> Dict[str, Any]:
        """Get live authentication status and QR code if required."""
        sender = self.repo.get_by_id(sender_id)
        if not sender:
            raise ValueError(f"Sender '{sender_id}' not found")

        auth_state = self.session_manager.get_auth_state(sender_id)
        return {
            "sender_id": sender.id,
            "status": sender.status.value,
            "channel": sender.channel.value,
            "display_name": sender.display_name,
            "identity": sender.identity,
            "qr_code": auth_state.get("qr_code"),
            "last_checked": auth_state.get("last_checked"),
            "error_message": auth_state.get("error_message"),
        }

    def confirm_whatsapp_auth(self, sender_id: str) -> Dict[str, Any]:
        """Confirm WhatsApp authentication (test/mock mode or manual verification)."""
        sender = self.repo.get_by_id(sender_id)
        if not sender:
            raise ValueError(f"Sender '{sender_id}' not found")

        sender.status = SenderStatus.ACTIVE
        self.repo.save(sender)
        self.session.commit()

        self.session_manager.confirm_mock_auth(
            sender_id=sender_id,
            on_event_callback=lambda evt, p: event_bus.publish_event(evt, p),
        )

        return {
            "sender_id": sender.id,
            "status": SenderStatus.ACTIVE.value,
            "channel": sender.channel.value,
            "message": "Session confirmed as ACTIVE",
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
        """Register or configure Email sender credentials and optionally verify connection."""
        clean_id = id.strip()
        sender = self.repo.get_by_id(clean_id)
        if not sender:
            sender = SenderAccount(
                id=clean_id,
                channel=Channel.EMAIL,
                provider="smtp",
                identity=identity.strip(),
                display_name=display_name.strip(),
                status=SenderStatus.AUTH_REQUIRED,
                daily_limit=100,
                hourly_limit=20,
            )
        else:
            sender.identity = identity.strip()
            sender.display_name = display_name.strip()

        # Set credentials on active email provider
        provider = get_email_provider()
        if hasattr(provider, "set_sender_credentials"):
            provider.set_sender_credentials(
                sender_account_id=clean_id,
                user=user or identity,
                password=password or "",
                host=host,
                port=port,
            )

        verification_error = None
        if verify_now and user and password:
            if hasattr(provider, "verify_credentials"):
                success, err = provider.verify_credentials(clean_id)
                if success:
                    sender.status = SenderStatus.ACTIVE
                else:
                    sender.status = SenderStatus.ERROR
                    verification_error = err
            else:
                sender.status = SenderStatus.ACTIVE
        else:
            if not user or not password:
                sender.status = SenderStatus.AUTH_REQUIRED

        self.repo.save(sender)
        self.session.commit()

        event_bus.publish_event(
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
            "verified": sender.status == SenderStatus.ACTIVE,
            "error_message": verification_error,
        }

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

        event_bus.publish_event(
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
        self.seed_defaults_if_empty()
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

