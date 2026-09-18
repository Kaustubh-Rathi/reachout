"""Campaign worker and outreach execution orchestrator.

Implements the transactional pre-send pattern, template rendering, pre-dispatch variable
validation, provider dispatch, error classification, quota accounting, and event publication.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.config import DEFAULT_MESSAGE_SUBJECT, SENDER_PROFILE
from app.domain.campaign import Campaign
from app.domain.enums import AUTH_FAILURE_CODES, AttemptType, Channel, OutreachStatus, SenderStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.sender_account import SenderAccount
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import DomainEvent, EventPublisher
from app.ports.providers import EmailProvider, ProviderSendResult, WhatsAppProvider


class OutreachWorker:
    """Coordinates the execution of a single outreach attempt with pre-send transactional guarantees."""

    def __init__(
        self,
        session_factory: Any,
        whatsapp_provider: WhatsAppProvider,
        email_provider: EmailProvider,
        rate_limiter: RateLimiter,
        event_publisher: EventPublisher,
        repository_factory: Any,
    ) -> None:
        self.session_factory = session_factory
        self.whatsapp_provider = whatsapp_provider
        self.email_provider = email_provider
        self.rate_limiter = rate_limiter
        self.event_publisher = event_publisher
        self.repository_factory = repository_factory

    def _record_pre_send_failure(
        self,
        session,
        outreach_repo,
        *,
        contact_id: str,
        sender_account_id: str,
        channel: Channel,
        attempt_type: AttemptType,
        campaign_id: Optional[str],
        destination: Optional[str],
        template: MessageTemplate,
        failure_code: str,
        failure_detail: str,
        now: datetime,
        salt_prefix: str,
        message_body: Optional[str] = None,
        subject: Optional[str] = None,
        attachment_ref: Optional[str] = None,
    ) -> OutreachAttempt:
        """Persist and publish a FAILED attempt for a pre-dispatch validation failure."""
        idemp_key = generate_idempotency_key(
            contact_id=contact_id,
            channel=channel,
            attempt_type=attempt_type,
            campaign_id=campaign_id,
            destination=destination,
            custom_salt=f"{salt_prefix}_{int(now.timestamp() * 1000)}",
        )
        failed_attempt = OutreachAttempt.prepare(
            contact_id=contact_id,
            sender_account_id=sender_account_id,
            channel=channel,
            attempt_type=attempt_type,
            message_body=message_body if message_body is not None else template.body,
            destination=destination,
            campaign_id=campaign_id,
            template_id=template.id,
            subject=subject if subject is not None else template.subject,
            attachment_ref=attachment_ref,
            idempotency_key=idemp_key,
            prepared_at=now,
        )
        failed_attempt.mark_failed(failure_code=failure_code, failure_detail=failure_detail, timestamp=now)
        outreach_repo.save(failed_attempt)
        session.commit()
        self.event_publisher.publish(
            DomainEvent(
                event_type="AttemptFailed",
                payload={
                    "attempt_id": failed_attempt.id,
                    "contact_id": contact_id,
                    "channel": channel.value,
                    "destination": destination,
                    "sender_account_id": sender_account_id,
                    "template_id": template.id,
                    "failure_code": failure_code,
                    "failure_detail": failure_detail,
                    "timestamp": now.isoformat(),
                },
            )
        )
        return failed_attempt

    def execute_attempt(
        self,
        contact_id: str,
        sender_account: SenderAccount,
        template: MessageTemplate,
        campaign: Optional[Campaign] = None,
        attempt_type: AttemptType = AttemptType.AUTOMATIC,
        custom_attachment_path: Optional[str] = None,
        custom_extra_vars: Optional[Dict[str, Any]] = None,
        destination: Optional[str] = None,
    ) -> OutreachAttempt:
        """Execute a single outreach attempt following the Pre-Send Transaction pattern."""
        now = datetime.now(timezone.utc)
        channel = template.channel
        campaign_id = campaign.id if campaign else None

        # --- Phase 1: Pre-Send Transaction & Variable Validation (Database Reservation) ---
        with self.session_factory() as session:
            repos = self.repository_factory(session)
            contact_repo = repos.contact
            company_repo = repos.company
            outreach_repo = repos.outreach

            contact = contact_repo.get_by_id(contact_id)
            if not contact:
                raise NotFoundError(f"Target contact '{contact_id}' not found")

            company = company_repo.get_by_id(contact.company_id) if contact.company_id else None

            # Resolve effective target destination
            effective_destination = destination
            if not effective_destination:
                if channel == Channel.WHATSAPP:
                    effective_destination = contact.primary_phone or ""
                elif channel == Channel.EMAIL:
                    effective_destination = contact.primary_email or ""

            # 0. Sender-session guard: never dispatch from a non-ACTIVE sender, even
            # when execute_attempt is called directly (the scheduler already filters,
            # but the worker is a public entry point and must enforce the invariant).
            if not sender_account.is_available():
                return self._record_pre_send_failure(
                    session,
                    outreach_repo,
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    template=template,
                    failure_code="ERR_SENDER_NOT_ACTIVE",
                    failure_detail=(
                        f"Sender '{sender_account.id}' is not ACTIVE "
                        f"(status={sender_account.status.value}); authenticate/reactivate it before sending."
                    ),
                    now=now,
                    salt_prefix="sender_inactive",
                )

            # 1. Template variable validation before any dispatch
            validation_errors = template.validate(contact=contact, company=company)
            if validation_errors:
                return self._record_pre_send_failure(
                    session,
                    outreach_repo,
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    template=template,
                    failure_code="ERR_TEMPLATE_VARIABLE_UNRESOLVED",
                    failure_detail="; ".join(validation_errors),
                    now=now,
                    salt_prefix="val_err",
                    attachment_ref=custom_attachment_path or template.attachment_ref,
                )

            # 2. Recipient handle validation
            if channel == Channel.WHATSAPP and (not effective_destination or not effective_destination.strip()):
                return self._record_pre_send_failure(
                    session,
                    outreach_repo,
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    template=template,
                    failure_code="ERR_PHONE_UNAVAILABLE",
                    failure_detail="Target contact has no valid phone number for WhatsApp",
                    now=now,
                    salt_prefix="no_phone",
                )

            if channel == Channel.EMAIL and (not effective_destination or not effective_destination.strip()):
                return self._record_pre_send_failure(
                    session,
                    outreach_repo,
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    template=template,
                    failure_code="ERR_EMAIL_UNAVAILABLE",
                    failure_detail="Target contact has no valid email address",
                    now=now,
                    salt_prefix="no_email",
                )

            # Render message template
            rendered = template.render(
                contact=contact,
                company=company,
                extra_vars=custom_extra_vars,
                sender_profile=SENDER_PROFILE,
            )
            attachment_to_use = custom_attachment_path or rendered.attachment_ref

            # Idempotency key generation
            idemp_key = generate_idempotency_key(
                contact_id=contact.contact_id,
                channel=channel,
                attempt_type=attempt_type,
                campaign_id=campaign_id,
                destination=effective_destination,
                custom_salt=str(int(now.timestamp() * 1000)) if attempt_type == AttemptType.RESEND else None,
            )

            # Check if duplicate attempt already exists
            existing_attempt = outreach_repo.get_by_idempotency_key(idemp_key)
            if existing_attempt:
                if existing_attempt.status == OutreachStatus.SENT:
                    return existing_attempt
                if existing_attempt.status in (OutreachStatus.QUEUED, OutreachStatus.SENDING):
                    return existing_attempt
                if existing_attempt.status == OutreachStatus.PREPARED:
                    attempt = existing_attempt
                else:
                    # Previous attempt reached a terminal non-sent status (FAILED, CANCELLED, etc.).
                    # A retry must generate a distinct idempotency key and create a fresh PREPARED attempt.
                    retry_key = generate_idempotency_key(
                        contact_id=contact.contact_id,
                        channel=channel,
                        attempt_type=attempt_type,
                        campaign_id=campaign_id,
                        destination=effective_destination,
                        custom_salt=str(int(now.timestamp() * 1000)),
                    )
                    attempt = OutreachAttempt.prepare(
                        contact_id=contact.contact_id,
                        sender_account_id=sender_account.id,
                        channel=channel,
                        attempt_type=attempt_type,
                        message_body=rendered.body,
                        destination=effective_destination,
                        campaign_id=campaign_id,
                        template_id=template.id,
                        subject=rendered.subject,
                        attachment_ref=attachment_to_use,
                        idempotency_key=retry_key,
                        prepared_at=now,
                    )
                    outreach_repo.save(attempt)
                    session.commit()
            else:
                # Create PREPARED attempt
                attempt = OutreachAttempt.prepare(
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    message_body=rendered.body,
                    destination=effective_destination,
                    campaign_id=campaign_id,
                    template_id=template.id,
                    subject=rendered.subject,
                    attachment_ref=attachment_to_use,
                    idempotency_key=idemp_key,
                    prepared_at=now,
                )
                outreach_repo.save(attempt)
                session.commit()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="AttemptPrepared",
                    payload={
                        "attempt_id": attempt.id,
                        "contact_id": contact.contact_id,
                        "channel": channel.value,
                        "destination": effective_destination,
                        "sender_account_id": sender_account.id,
                        "template_id": template.id,
                        "timestamp": now.isoformat(),
                    },
                )
            )

        # --- Phase 2: Rate Limiter & Concurrency Acquisition ---
        min_channel_delay = self.rate_limiter.default_channel_delay.get(channel.name, 2.0)
        pacing_timeout = max(float(min_channel_delay) + 10.0, 10.0)

        is_ready = self.rate_limiter.wait_for_ready(
            sender_id=sender_account.id,
            channel=channel.value,
            daily_limit=sender_account.daily_limit,
            hourly_limit=sender_account.hourly_limit,
            timeout_seconds=pacing_timeout,
        )

        if not is_ready:
            # Pacing timeout! Do NOT call provider.send(), do NOT mark attempt SENT, do NOT advance coverage
            with self.session_factory() as session:
                outreach_repo = self.repository_factory(session).outreach
                db_attempt = outreach_repo.get_by_id(attempt.id)
                if db_attempt:
                    db_attempt.mark_failed(
                        failure_code="ERR_PACING_TIMEOUT",
                        failure_detail=f"Sender '{sender_account.id}' not ready within pacing timeout ({pacing_timeout}s)",
                        timestamp=datetime.now(timezone.utc),
                    )
                    outreach_repo.save(db_attempt)
                    session.commit()
                    attempt = db_attempt

            self.event_publisher.publish(
                DomainEvent(
                    event_type="AttemptFailed",
                    payload={
                        "attempt_id": attempt.id,
                        "contact_id": attempt.contact_id,
                        "channel": channel.value,
                        "destination": attempt.destination,
                        "sender_account_id": sender_account.id,
                        "template_id": template.id,
                        "status": "FAILED",
                        "failure_code": "ERR_PACING_TIMEOUT",
                        "failure_detail": attempt.failure_detail,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
            return attempt

        acquired = self.rate_limiter.acquire_sender(sender_account.id)
        if not acquired:
            with self.session_factory() as session:
                outreach_repo = self.repository_factory(session).outreach
                db_attempt = outreach_repo.get_by_id(attempt.id)
                if db_attempt:
                    db_attempt.mark_failed(
                        failure_code="ERR_SENDER_BUSY",
                        failure_detail=f"Sender '{sender_account.id}' locked by concurrent in-flight dispatch",
                        timestamp=datetime.now(timezone.utc),
                    )
                    outreach_repo.save(db_attempt)
                    session.commit()
                    attempt = db_attempt
            return attempt

        try:
            # --- Phase 3: Transition to SENDING ---
            with self.session_factory() as session:
                outreach_repo = self.repository_factory(session).outreach
                db_attempt = outreach_repo.get_by_id(attempt.id)
                if db_attempt:
                    if db_attempt.status in (OutreachStatus.PREPARED, OutreachStatus.QUEUED):
                        db_attempt.mark_sending()
                        outreach_repo.save(db_attempt)
                        session.commit()
                        attempt = db_attempt
                    elif db_attempt.status == OutreachStatus.SENDING:
                        attempt = db_attempt
                    else:
                        raise ValidationError(f"Cannot begin sending from status '{db_attempt.status.value}'")

            self.event_publisher.publish(
                DomainEvent(
                    event_type="AttemptStarted",
                    payload={
                        "attempt_id": attempt.id,
                        "contact_id": attempt.contact_id,
                        "channel": channel.value,
                        "destination": attempt.destination,
                        "sender_account_id": sender_account.id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )

            # --- Phase 4: Provider External I/O ---
            provider_result: ProviderSendResult
            try:
                if channel == Channel.WHATSAPP:
                    if not self.whatsapp_provider:
                        raise RuntimeError("WhatsAppProvider is not configured on OutreachWorker")
                    phone_target = attempt.destination or contact.primary_phone or ""
                    provider_result = self.whatsapp_provider.send_message(
                        attempt=attempt,
                        recipient_phone=phone_target,
                        message_body=attempt.message_body_snapshot,
                        attachment_path=attempt.attachment_snapshot,
                    )
                elif channel == Channel.EMAIL:
                    email_target = attempt.destination or contact.primary_email or ""
                    provider_result = self.email_provider.send_email(
                        attempt=attempt,
                        recipient_email=email_target,
                        subject=attempt.subject_snapshot or DEFAULT_MESSAGE_SUBJECT,
                        message_body=attempt.message_body_snapshot,
                        attachment_path=attempt.attachment_snapshot,
                    )
                else:
                    provider_result = ProviderSendResult.failed(
                        "ERR_UNSUPPORTED_CHANNEL", f"Channel {channel} not supported"
                    )
            except Exception as exc:
                provider_result = ProviderSendResult.unknown(
                    reason=f"Unhandled exception during provider send execution: {exc}"
                )

            # --- Phase 5: Post-Send State Resolution & Quota Accounting ---
            post_now = datetime.now(timezone.utc)
            with self.session_factory() as session:
                repos = self.repository_factory(session)
                outreach_repo = repos.outreach
                contact_repo = repos.contact
                sender_repo = repos.sender
                campaign_repo = repos.campaign

                db_attempt = outreach_repo.get_by_id(attempt.id) or attempt
                db_contact = contact_repo.get_by_id(attempt.contact_id)
                db_sender = sender_repo.get_by_id(sender_account.id)
                db_campaign = campaign_repo.get_by_id(campaign_id) if campaign_id else None

                if provider_result.success:
                    db_attempt.mark_sent(
                        provider_reference=provider_result.provider_reference,
                        timestamp=post_now,
                    )
                    if db_contact:
                        db_contact.record_outreach_success(channel=channel, timestamp=post_now)
                        contact_repo.save(db_contact)

                    if db_sender:
                        db_sender.record_usage(post_now)
                        sender_repo.save(db_sender)

                    if db_campaign:
                        if attempt_type == AttemptType.AUTOMATIC:
                            campaign_repo.increment_automatic_used(campaign_id)
                        else:
                            campaign_repo.increment_manual_used(campaign_id)

                    self.rate_limiter.record_dispatch_success(sender_account.id)
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="AttemptSent",
                            payload={
                                "attempt_id": db_attempt.id,
                                "contact_id": db_attempt.contact_id,
                                "channel": channel.value,
                                "destination": db_attempt.destination,
                                "sender_account_id": sender_account.id,
                                "template_id": template.id,
                                "status": "SENT",
                                "provider_reference": db_attempt.provider_reference,
                                "timestamp": post_now.isoformat(),
                            },
                        )
                    )

                elif provider_result.status == OutreachStatus.FAILED:
                    db_attempt.mark_failed(
                        failure_code=provider_result.failure_code or "ERR_PROVIDER_FAILED",
                        failure_detail=provider_result.failure_detail or "Delivery failed",
                        timestamp=post_now,
                    )
                    is_rate_limit = "RATE_LIMIT" in (provider_result.failure_code or "")
                    self.rate_limiter.record_dispatch_failure(sender_account.id, is_rate_limit=is_rate_limit)

                    # A provider auth failure means the session died: downgrade the
                    # sender so the readiness gate and rotation stop selecting it.
                    if db_sender and (provider_result.failure_code or "") in AUTH_FAILURE_CODES:
                        db_sender.mark_status(SenderStatus.AUTH_REQUIRED)
                        sender_repo.save(db_sender)
                        self.event_publisher.publish(
                            DomainEvent(
                                event_type="SENDER_STATUS_CHANGED",
                                payload={
                                    "sender_id": db_sender.id,
                                    "channel": channel.value,
                                    "status": SenderStatus.AUTH_REQUIRED.value,
                                    "reason": provider_result.failure_code,
                                    "timestamp": post_now.isoformat(),
                                },
                            )
                        )

                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="AttemptFailed",
                            payload={
                                "attempt_id": db_attempt.id,
                                "contact_id": db_attempt.contact_id,
                                "channel": channel.value,
                                "destination": db_attempt.destination,
                                "sender_account_id": sender_account.id,
                                "template_id": template.id,
                                "status": "FAILED",
                                "failure_code": db_attempt.failure_code,
                                "failure_detail": db_attempt.failure_detail,
                                "timestamp": post_now.isoformat(),
                            },
                        )
                    )

                else:
                    # UNKNOWN or RECOVERY_REQUIRED
                    if provider_result.status == OutreachStatus.RECOVERY_REQUIRED:
                        db_attempt.mark_recovery_required(
                            reason=provider_result.failure_detail or "Ambiguous external outcome",
                            timestamp=post_now,
                        )
                    else:
                        db_attempt.mark_unknown(
                            reason=provider_result.failure_detail or "Unknown delivery state",
                            timestamp=post_now,
                        )
                    self.rate_limiter.record_dispatch_failure(sender_account.id)
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="AttemptUnknown",
                            payload={
                                "attempt_id": db_attempt.id,
                                "contact_id": db_attempt.contact_id,
                                "channel": channel.value,
                                "destination": db_attempt.destination,
                                "sender_account_id": sender_account.id,
                                "status": db_attempt.status.value,
                                "reason": db_attempt.failure_detail,
                                "timestamp": post_now.isoformat(),
                            },
                        )
                    )

                outreach_repo.save(db_attempt)
                session.commit()
                attempt = db_attempt

        finally:
            self.rate_limiter.release_sender(sender_account.id)

        return attempt
