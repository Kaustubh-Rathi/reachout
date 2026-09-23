"""Campaign worker and outreach execution orchestrator.

Implements the transactional pre-send pattern, template rendering, pre-dispatch variable
validation, provider dispatch, error classification, quota accounting, and event publication.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.config import DEFAULT_MESSAGE_SUBJECT, SENDER_PROFILE
from app.domain.campaign import Campaign
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.sender_account import SenderAccount
from app.infrastructure.scheduler.post_send_resolver import PostSendResolver
from app.infrastructure.scheduler.pre_send_validator import PreSendValidator
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import Clock, DomainEvent, EventPublisher
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
        clock: Clock,
    ) -> None:
        self.session_factory = session_factory
        self.whatsapp_provider = whatsapp_provider
        self.email_provider = email_provider
        self.rate_limiter = rate_limiter
        self.event_publisher = event_publisher
        self.repository_factory = repository_factory
        self.clock = clock
        self.post_send_resolver = PostSendResolver(rate_limiter=rate_limiter, event_publisher=event_publisher)

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
                event_type="ATTEMPT_FAILED",
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
        now = self.clock.now()
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

            # 0-2. Pre-send validation: sender-session, template variables, recipient.
            failure = PreSendValidator.validate(
                sender_account=sender_account,
                template=template,
                contact=contact,
                company=company,
                channel=channel,
                destination=effective_destination or "",
                custom_attachment_path=custom_attachment_path,
            )
            if failure is not None:
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
                    failure_code=failure.code,
                    failure_detail=failure.detail,
                    now=now,
                    salt_prefix=failure.salt_prefix,
                    attachment_ref=failure.attachment_ref,
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
                    # Previous attempt reached a terminal non-sent status (FAILED, UNKNOWN, RECOVERY_REQUIRED).
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
                    event_type="ATTEMPT_PREPARED",
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
                        timestamp=self.clock.now(),
                    )
                    outreach_repo.save(db_attempt)
                    session.commit()
                    attempt = db_attempt

            self.event_publisher.publish(
                DomainEvent(
                    event_type="ATTEMPT_FAILED",
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
                        "timestamp": self.clock.now().isoformat(),
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
                        timestamp=self.clock.now(),
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
                    event_type="ATTEMPT_STARTED",
                    payload={
                        "attempt_id": attempt.id,
                        "contact_id": attempt.contact_id,
                        "channel": channel.value,
                        "destination": attempt.destination,
                        "sender_account_id": sender_account.id,
                        "timestamp": self.clock.now().isoformat(),
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
            post_now = self.clock.now()
            with self.session_factory() as session:
                attempt = self.post_send_resolver.resolve(
                    session=session,
                    repository_factory=self.repository_factory,
                    attempt=attempt,
                    sender_account=sender_account,
                    campaign_id=campaign_id,
                    attempt_type=attempt_type,
                    channel=channel,
                    template=template,
                    provider_result=provider_result,
                    now=post_now,
                )

        finally:
            self.rate_limiter.release_sender(sender_account.id)

        return attempt
