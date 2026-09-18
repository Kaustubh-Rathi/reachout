"""Outbound outreach application service.

Coordinates single sends, manual resends, provider dispatching, audit history,
and recovery handling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.contact import Contact
from app.domain.enums import AUTH_FAILURE_CODES, AttemptType, Channel, OutreachStatus, SenderStatus
from app.domain.errors import AlreadySentError, ConflictError, NotFoundError, ValidationError
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.sender_account import SenderAccount
from app.ports.providers import EmailProvider, ProviderSendResult, WhatsAppProvider
from app.services.channel_defaults import channel_label
from app.services.context import ServiceContext, build_service_context
from app.services.outreach_message_composer import MessageComposer
from app.services.outreach_recovery_service import OutreachRecoveryService


class OutreachService:
    """Application service for executing and recording outbound messaging."""

    def __init__(
        self,
        session: Session,
        whatsapp_provider: Optional[WhatsAppProvider] = None,
        email_provider: Optional[EmailProvider] = None,
        rate_limiter=None,
        event_publisher=None,
        context: Optional[ServiceContext] = None,
    ) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.contact_repo = ctx.contact_repo
        self.outreach_repo = ctx.outreach_repo
        self.sender_repo = ctx.sender_repo
        self.template_repo = ctx.template_repo

        # Resolve providers from explicit injection or the composed context.
        self.whatsapp_provider = whatsapp_provider if whatsapp_provider is not None else ctx.whatsapp_provider
        self.email_provider = email_provider if email_provider is not None else ctx.email_provider
        # N1: manual sends share the app-wide rate limiter used by campaigns.
        self.rate_limiter = rate_limiter if rate_limiter is not None else ctx.rate_limiter
        # Event publishing is injectable so the service is unit-testable with a fake bus.
        self.event_publisher = event_publisher if event_publisher is not None else ctx.event_publisher
        self.clock = ctx.clock
        self.composer = MessageComposer(template_repo=self.template_repo)
        self.recovery = OutreachRecoveryService(
            session=session,
            outreach_repo=self.outreach_repo,
            contact_repo=self.contact_repo,
            clock=self.clock,
        )

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------
    def _validate_sender_active(self, sender: SenderAccount) -> None:
        """B13: refuse to dispatch from a sender that is not ACTIVE."""
        if not sender.is_available():
            raise ValidationError(
                f"SENDER_NOT_ACTIVE: Sender '{sender.id}' is not ACTIVE (status={sender.status.value}). "
                f"Authenticate/reactivate it before sending."
            )

    def _require_contact(self, contact_id: str) -> Contact:
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise NotFoundError(f"Contact not found: {contact_id}")
        return contact

    def _resolve_sender(self, sender_id: Optional[str], channel: Channel, contact_id: str) -> SenderAccount:
        sender = None
        if sender_id:
            sender = self.sender_repo.get_by_id(sender_id)
            if not sender:
                raise NotFoundError(f"Sender account not found: {sender_id}")
        if not sender:
            active_senders = self.sender_repo.list_active(channel)
            sender = active_senders[0] if active_senders else None
        if not sender:
            label = channel_label(channel)
            raise ValidationError(
                f"NO_ACTIVE_{label.upper()}_SESSION: No active {label} sender account available for {contact_id}"
            )
        # B13: never dispatch from a non-ACTIVE sender, even when explicitly selected.
        self._validate_sender_active(sender)
        return sender

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    def _check_and_build_idempotency(
        self,
        contact_id: str,
        channel: Channel,
        attempt_type: AttemptType,
        campaign_id: Optional[str],
        destination: Optional[str],
        now: datetime,
    ) -> str:
        """B14: deterministic idempotency key + pre-dispatch dedup guard.

        Returns an idempotency key safe to insert. If an identical attempt was already
        SENT, raises a dedicated AlreadySentError; if one is in-flight, raises ValueError.
        """
        base_key = generate_idempotency_key(
            contact_id=contact_id,
            channel=channel,
            attempt_type=attempt_type,
            campaign_id=campaign_id,
            destination=destination,
        )
        existing = self.outreach_repo.get_by_idempotency_key(base_key)
        if existing:
            # Already delivered to this endpoint -> never send a duplicate (B14).
            if existing.status == OutreachStatus.SENT:
                raise AlreadySentError(existing.id)
            if existing.status in (OutreachStatus.QUEUED, OutreachStatus.SENDING):
                raise ConflictError("Outreach attempt already in-flight for this contact/channel/destination")
            # Previous attempt FAILED / UNKNOWN / RECOVERY: a genuine retry gets a distinct key.
            return generate_idempotency_key(
                contact_id=contact_id,
                channel=channel,
                attempt_type=attempt_type,
                campaign_id=campaign_id,
                destination=destination,
                custom_salt=str(int(now.timestamp() * 1000)),
            )
        return base_key

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch(
        self,
        channel: Channel,
        attempt: OutreachAttempt,
        recipient: str,
        subject: str,
        body: str,
        attachment_ref: Optional[str],
        sender: SenderAccount,
        now: datetime,
    ) -> tuple[OutreachAttempt, ProviderSendResult]:
        """N1: rate-limit, acquire sender, dispatch via the channel provider, release."""
        base_pace = self.rate_limiter.default_channel_delay.get(channel.value, 120.0)
        manual_pace = base_pace / 2.0
        ready = self.rate_limiter.wait_for_ready(
            sender_id=sender.id,
            channel=channel.value,
            daily_limit=sender.daily_limit,
            hourly_limit=sender.hourly_limit,
            timeout_seconds=manual_pace + 10.0,
            min_delay_override=manual_pace,
        )
        if not ready:
            attempt.mark_failed("ERR_PACING_TIMEOUT", "Sender not ready within pacing timeout", now)
            self.outreach_repo.save(attempt)
            self.session.commit()
            return attempt, ProviderSendResult.failed("ERR_PACING_TIMEOUT", "Sender not ready within pacing timeout")
        if not self.rate_limiter.acquire_sender(sender.id):
            attempt.mark_failed("ERR_SENDER_BUSY", "Sender locked by concurrent dispatch", now)
            self.outreach_repo.save(attempt)
            self.session.commit()
            return attempt, ProviderSendResult.failed("ERR_SENDER_BUSY", "Sender locked by concurrent dispatch")
        try:
            if channel == Channel.WHATSAPP:
                res = self.whatsapp_provider.send_message(
                    attempt=attempt,
                    recipient_phone=recipient,
                    message_body=body,
                    attachment_path=attachment_ref,
                )
            else:
                res = self.email_provider.send_email(
                    attempt=attempt,
                    recipient_email=recipient,
                    subject=subject,
                    message_body=body,
                    attachment_path=attachment_ref,
                )
        finally:
            self.rate_limiter.release_sender(sender.id)
        if res.success:
            self.rate_limiter.record_dispatch_success(sender.id)
        else:
            self.rate_limiter.record_dispatch_failure(
                sender.id, is_rate_limit=("RATE_LIMIT" in (res.failure_code or ""))
            )
        return attempt, res

    # ------------------------------------------------------------------
    # Result / event helpers
    # ------------------------------------------------------------------
    def _publish(self, event_type: str, attempt, contact, channel, recipient, sender, template, **extra) -> None:
        payload = {
            "attempt_id": attempt.id,
            "contact_id": contact.contact_id,
            "channel": channel.value,
            "recipient": recipient,
            "destination": recipient,
            "sender_account_id": sender.id,
            "template_id": template.id if template else None,
        }
        payload.update(extra)
        self.event_publisher.publish_event(event_type, payload)

    def _duplicate_result(self, attempt_id, channel, attempt_type, recipient, sender, template) -> Dict[str, Any]:
        return {
            "attempt_id": attempt_id,
            "status": "SENT",
            "success": True,
            "channel": channel.value,
            "attempt_type": attempt_type.value,
            "destination": recipient,
            "sender_account_id": sender.id,
            "template_id": template.id if template else None,
            "provider_reference": None,
            "failure_code": None,
            "failure_detail": None,
            "duplicate": True,
        }

    def _result(self, attempt, res, channel, recipient, sender, template) -> Dict[str, Any]:
        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "channel": channel.value,
            "attempt_type": attempt.attempt_type.value,
            "destination": recipient,
            "sender_account_id": sender.id,
            "template_id": template.id if template else None,
            "provider_reference": res.provider_reference,
            "failure_code": attempt.failure_code,
            "failure_detail": attempt.failure_detail,
        }

    def _finalize_send(self, attempt, res, contact, channel, recipient, sender, template, now) -> None:
        """Resolve the terminal state for a first-time send and emit its events."""
        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(channel, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()
            sent_payload = {
                "provider_reference": res.provider_reference,
                "status": "SENT",
                "timestamp": now.isoformat(),
            }
            self._publish("MESSAGE_SENT", attempt, contact, channel, recipient, sender, template, **sent_payload)
            self._publish("OUTREACH_SENT", attempt, contact, channel, recipient, sender, template, **sent_payload)
        elif res.status == OutreachStatus.RECOVERY_REQUIRED:
            attempt.mark_recovery_required(res.failure_detail or "Unknown recovery condition", now)
            self.outreach_repo.save(attempt)
            self.session.commit()
            self._publish(
                "OUTREACH_RECOVERY_REQUIRED",
                attempt,
                contact,
                channel,
                recipient,
                sender,
                template,
                reason=attempt.failure_detail,
            )
        else:
            attempt.mark_failed(res.failure_code or "ERR_SEND_FAILED", res.failure_detail or "Dispatch error", now)
            self.outreach_repo.save(attempt)
            # A provider auth failure means the session died: downgrade the sender
            # so readiness/rotation stop selecting it.
            if (res.failure_code or "") in AUTH_FAILURE_CODES:
                sender.mark_status(SenderStatus.AUTH_REQUIRED)
                self.sender_repo.save(sender)
                self.event_publisher.publish_event(
                    "SENDER_STATUS_CHANGED",
                    {
                        "sender_id": sender.id,
                        "channel": channel.value,
                        "status": SenderStatus.AUTH_REQUIRED.value,
                        "reason": res.failure_code,
                        "timestamp": now.isoformat(),
                    },
                )
            self.session.commit()
            failed_payload = {
                "status": "FAILED",
                "failure_code": attempt.failure_code,
                "failure_detail": attempt.failure_detail,
                "timestamp": now.isoformat(),
            }
            self._publish("MESSAGE_FAILED", attempt, contact, channel, recipient, sender, template, **failed_payload)
            self._publish("OUTREACH_FAILED", attempt, contact, channel, recipient, sender, template, **failed_payload)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_whatsapp(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        campaign_id: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch a single WhatsApp message and record attempt."""
        return self._send(
            Channel.WHATSAPP,
            contact_id,
            sender_id,
            template_id,
            custom_body,
            None,
            attachment_ref,
            campaign_id,
            destination,
        )

    def send_email(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        campaign_id: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch a single Email message and record attempt."""
        return self._send(
            Channel.EMAIL,
            contact_id,
            sender_id,
            template_id,
            custom_body,
            subject,
            attachment_ref,
            campaign_id,
            destination,
        )

    def _send(
        self,
        channel: Channel,
        contact_id: str,
        sender_id: Optional[str],
        template_id: Optional[str],
        custom_body: Optional[str],
        subject: Optional[str],
        attachment_ref: Optional[str],
        campaign_id: Optional[str],
        destination: Optional[str],
    ) -> Dict[str, Any]:
        contact = self._require_contact(contact_id)
        recipient = self.composer.resolve_recipient(contact, channel, destination)
        sender = self._resolve_sender(sender_id, channel, contact_id)
        body, subj, attachment_ref, template = self.composer.resolve_message(
            contact, channel, template_id, custom_body, subject, attachment_ref, is_resend=False
        )

        now = self.clock.now()
        attempt_type = AttemptType.AUTOMATIC if campaign_id else AttemptType.MANUAL
        # B14: deterministic idempotency key + pre-dispatch dedup guard.
        try:
            idempotency_key = self._check_and_build_idempotency(
                contact.contact_id, channel, attempt_type, campaign_id, recipient, now
            )
        except AlreadySentError as exc:
            return self._duplicate_result(exc.attempt_id, channel, attempt_type, recipient, sender, template)

        attempt = OutreachAttempt.prepare(
            contact_id=contact.contact_id,
            sender_account_id=sender.id,
            channel=channel,
            attempt_type=attempt_type,
            message_body=body,
            destination=recipient,
            campaign_id=campaign_id,
            template_id=template.id if template else None,
            subject=subj,
            attachment_ref=attachment_ref,
            idempotency_key=idempotency_key,
            prepared_at=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()
        self._publish("MESSAGE_PREPARED", attempt, contact, channel, recipient, sender, template)
        self._publish("OUTREACH_PREPARED", attempt, contact, channel, recipient, sender, template)

        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)
        self.session.commit()
        self._publish("OUTREACH_STARTED", attempt, contact, channel, recipient, sender, template)

        attempt, res = self._dispatch(channel, attempt, recipient, subj, body, attachment_ref, sender, now)
        self._finalize_send(attempt, res, contact, channel, recipient, sender, template, now)
        return self._result(attempt, res, channel, recipient, sender, template)

    def resend_whatsapp(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicit operator manual WhatsApp resend creating a new immutable OutreachAttempt."""
        return self._resend(
            Channel.WHATSAPP,
            contact_id,
            sender_id,
            template_id,
            custom_body,
            None,
            attachment_ref,
            destination,
        )

    def resend_email(
        self,
        contact_id: str,
        sender_id: Optional[str] = None,
        template_id: Optional[str] = None,
        subject: Optional[str] = None,
        custom_body: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        destination: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicit operator manual email resend creating a new immutable OutreachAttempt."""
        return self._resend(
            Channel.EMAIL,
            contact_id,
            sender_id,
            template_id,
            custom_body,
            subject,
            attachment_ref,
            destination,
        )

    def _resend(
        self,
        channel: Channel,
        contact_id: str,
        sender_id: Optional[str],
        template_id: Optional[str],
        custom_body: Optional[str],
        subject: Optional[str],
        attachment_ref: Optional[str],
        destination: Optional[str],
    ) -> Dict[str, Any]:
        contact = self._require_contact(contact_id)
        recipient = self.composer.resolve_recipient(contact, channel, destination)
        sender = self._resolve_sender(sender_id, channel, contact_id)
        body, subj, attachment_ref, template = self.composer.resolve_message(
            contact, channel, template_id, custom_body, subject, attachment_ref, is_resend=True
        )

        historical = self.outreach_repo.list_by_contact(contact.contact_id)
        now = self.clock.now()
        attempt = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=channel,
            rendered_body=body,
            subject=subj,
            attachment_ref=attachment_ref,
            template_id=template.id if template else None,
            destination=recipient,
            historical_attempts=historical,
            resend_timestamp=now,
        )
        self.outreach_repo.save(attempt)
        self.session.commit()

        # Dispatch (persist the SENDING transition before any external I/O).
        attempt.mark_sending(now)
        self.outreach_repo.save(attempt)
        self.session.commit()

        attempt, res = self._dispatch(channel, attempt, recipient, subj, body, attachment_ref, sender, now)

        if res.success:
            attempt.mark_sent(provider_reference=res.provider_reference, timestamp=now)
            contact.record_outreach_success(channel, now)
            self.contact_repo.save(contact)
            self.outreach_repo.save(attempt)
            self.session.commit()
            self._publish(
                "MESSAGE_SENT",
                attempt,
                contact,
                channel,
                recipient,
                sender,
                template,
                attempt_type="RESEND",
            )
        else:
            attempt.mark_failed(res.failure_code or "ERR_RESEND_FAILED", res.failure_detail or "Dispatch error", now)
            self.outreach_repo.save(attempt)
            if (res.failure_code or "") in AUTH_FAILURE_CODES:
                sender.mark_status(SenderStatus.AUTH_REQUIRED)
                self.sender_repo.save(sender)
                self.event_publisher.publish_event(
                    "SENDER_STATUS_CHANGED",
                    {
                        "sender_id": sender.id,
                        "channel": channel.value,
                        "status": SenderStatus.AUTH_REQUIRED.value,
                        "reason": res.failure_code,
                        "timestamp": now.isoformat(),
                    },
                )
            self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "success": res.success,
            "destination": recipient,
            "provider_reference": res.provider_reference,
            "attempt_type": "RESEND",
        }

    def get_history(self, contact_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete historical attempts for a contact."""
        return self.recovery.get_history(contact_id)

    def get_recovery_queue(self) -> List[Dict[str, Any]]:
        """Retrieve all outreach attempts stuck in RECOVERY_REQUIRED or UNKNOWN."""
        return self.recovery.get_recovery_queue()

    def resolve_recovery(self, attempt_id: str, action: str, recovery_notes: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a stuck recovery attempt: 'mark_sent', 'retry', or 'cancel'."""
        return self.recovery.resolve_recovery(attempt_id, action, recovery_notes)
