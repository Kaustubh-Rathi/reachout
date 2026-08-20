"""Campaign worker and outreach execution orchestrator.

Implements the transactional pre-send pattern, template rendering, pre-dispatch variable
validation, provider dispatch, error classification, quota accounting, and event publication.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from sqlalchemy.orm import Session

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.sender_account import SenderAccount
from app.infrastructure.events.event_bus import default_event_bus
from app.infrastructure.providers.factory import get_email_provider, get_whatsapp_provider
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import DomainEvent, EventPublisher
from app.ports.providers import EmailProvider, ProviderSendResult, WhatsAppProvider


class OutreachWorker:
    """Coordinates the execution of a single outreach attempt with pre-send transactional guarantees."""

    def __init__(
        self,
        session_factory: Any,
        whatsapp_provider: Optional[WhatsAppProvider] = None,
        email_provider: Optional[EmailProvider] = None,
        rate_limiter: Optional[RateLimiter] = None,
        event_publisher: Optional[EventPublisher] = None,
    ) -> None:
        self.session_factory = session_factory
        self.whatsapp_provider = whatsapp_provider if whatsapp_provider is not None else get_whatsapp_provider()
        self.email_provider = email_provider if email_provider is not None else get_email_provider()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.event_publisher = event_publisher or default_event_bus

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
            contact_repo = SqliteContactRepository(session)
            company_repo = SqliteCompanyRepository(session)
            outreach_repo = SqliteOutreachRepository(session)

            contact = contact_repo.get_by_id(contact_id)
            if not contact:
                raise ValueError(f"Target contact '{contact_id}' not found")

            company = company_repo.get_by_id(contact.company_id) if contact.company_id else None

            # Resolve effective target destination
            effective_destination = destination
            if not effective_destination:
                if channel == Channel.WHATSAPP:
                    effective_destination = contact.primary_phone if hasattr(contact, "primary_phone") else (contact.phone or "")
                elif channel == Channel.EMAIL:
                    effective_destination = contact.primary_email if hasattr(contact, "primary_email") else (contact.email or "")

            # 1. Template variable validation before any dispatch
            validation_errors = template.validate(contact=contact, company=company)
            if validation_errors:
                # Idempotency key generation for validation failure
                idemp_key = generate_idempotency_key(
                    contact_id=contact.contact_id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    custom_salt=f"val_err_{int(now.timestamp() * 1000)}",
                )
                failed_attempt = OutreachAttempt.prepare(
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    message_body=template.body,
                    destination=effective_destination,
                    campaign_id=campaign_id,
                    template_id=template.id,
                    subject=template.subject,
                    attachment_ref=custom_attachment_path or template.attachment_ref,
                    idempotency_key=idemp_key,
                    prepared_at=now,
                )
                failed_attempt.mark_failed(
                    failure_code="ERR_TEMPLATE_VARIABLE_UNRESOLVED",
                    failure_detail="; ".join(validation_errors),
                    timestamp=now,
                )
                outreach_repo.save(failed_attempt)
                session.commit()

                self.event_publisher.publish(
                    DomainEvent(
                        event_type="AttemptFailed",
                        payload={
                            "attempt_id": failed_attempt.id,
                            "contact_id": contact.contact_id,
                            "channel": channel.value,
                            "destination": effective_destination,
                            "sender_account_id": sender_account.id,
                            "template_id": template.id,
                            "failure_code": failed_attempt.failure_code,
                            "failure_detail": failed_attempt.failure_detail,
                            "timestamp": now.isoformat(),
                        },
                    )
                )
                return failed_attempt

            # 2. Recipient handle validation
            if channel == Channel.WHATSAPP and (not effective_destination or not effective_destination.strip()):
                idemp_key = generate_idempotency_key(
                    contact_id=contact.contact_id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    custom_salt=f"no_phone_{int(now.timestamp() * 1000)}",
                )
                failed_attempt = OutreachAttempt.prepare(
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    message_body=template.body,
                    destination=effective_destination,
                    campaign_id=campaign_id,
                    template_id=template.id,
                    idempotency_key=idemp_key,
                    prepared_at=now,
                )
                failed_attempt.mark_failed(
                    failure_code="ERR_PHONE_UNAVAILABLE",
                    failure_detail="Target contact has no valid phone number for WhatsApp",
                    timestamp=now,
                )
                outreach_repo.save(failed_attempt)
                session.commit()
                return failed_attempt

            if channel == Channel.EMAIL and (not effective_destination or not effective_destination.strip()):
                idemp_key = generate_idempotency_key(
                    contact_id=contact.contact_id,
                    channel=channel,
                    attempt_type=attempt_type,
                    campaign_id=campaign_id,
                    destination=effective_destination,
                    custom_salt=f"no_email_{int(now.timestamp() * 1000)}",
                )
                failed_attempt = OutreachAttempt.prepare(
                    contact_id=contact.contact_id,
                    sender_account_id=sender_account.id,
                    channel=channel,
                    attempt_type=attempt_type,
                    message_body=template.body,
                    destination=effective_destination,
                    campaign_id=campaign_id,
                    template_id=template.id,
                    idempotency_key=idemp_key,
                    prepared_at=now,
                )
                failed_attempt.mark_failed(
                    failure_code="ERR_EMAIL_UNAVAILABLE",
                    failure_detail="Target contact has no valid email address",
                    timestamp=now,
                )
                outreach_repo.save(failed_attempt)
                session.commit()
                return failed_attempt

            # Render message template
            rendered = template.render(contact=contact, company=company, extra_vars=custom_extra_vars)
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
                attempt = existing_attempt
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
        min_channel_delay = self.rate_limiter.default_channel_delay.get(
            channel.name if hasattr(channel, "name") else str(channel).upper(), 2.0
        )
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
                outreach_repo = SqliteOutreachRepository(session)
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
                outreach_repo = SqliteOutreachRepository(session)
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
                outreach_repo = SqliteOutreachRepository(session)
                db_attempt = outreach_repo.get_by_id(attempt.id)
                if db_attempt:
                    db_attempt.mark_sending()
                    outreach_repo.save(db_attempt)
                    session.commit()
                    attempt = db_attempt

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
                    phone_target = attempt.destination or (contact.primary_phone if hasattr(contact, "primary_phone") else contact.phone) or ""
                    provider_result = self.whatsapp_provider.send_message(
                        attempt=attempt,
                        recipient_phone=phone_target,
                        message_body=attempt.message_body_snapshot,
                        attachment_path=attempt.attachment_snapshot,
                    )
                elif channel == Channel.EMAIL:
                    if not self.email_provider:
                        raise RuntimeError("EmailProvider is not configured on OutreachWorker")
                    email_target = attempt.destination or (contact.primary_email if hasattr(contact, "primary_email") else contact.email) or ""
                    provider_result = self.email_provider.send_email(
                        attempt=attempt,
                        recipient_email=email_target,
                        subject=attempt.subject_snapshot or "Exploring opportunities",
                        message_body=attempt.message_body_snapshot,
                        attachment_path=attempt.attachment_snapshot,
                    )
                else:
                    provider_result = ProviderSendResult.failed("ERR_UNSUPPORTED_CHANNEL", f"Channel {channel} not supported")
            except Exception as exc:
                provider_result = ProviderSendResult.unknown(
                    reason=f"Unhandled exception during provider send execution: {exc}"
                )

            # --- Phase 5: Post-Send State Resolution & Quota Accounting ---
            post_now = datetime.now(timezone.utc)
            with self.session_factory() as session:
                outreach_repo = SqliteOutreachRepository(session)
                contact_repo = SqliteContactRepository(session)
                sender_repo = SqliteSenderRepository(session)
                campaign_repo = SqliteCampaignRepository(session)

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
                            db_campaign.record_automatic_dispatch(success=True)
                        else:
                            db_campaign.record_manual_dispatch()
                        campaign_repo.save(db_campaign)

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
