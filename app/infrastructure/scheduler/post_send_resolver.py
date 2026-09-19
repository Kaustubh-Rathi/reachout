"""Post-send state resolution and quota accounting.

Applies the provider outcome to the attempt, contact, sender, and campaign
(persisted in one commit), records pacing results, and publishes events.
Kept separate from pre-send validation and dispatch.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.enums import AUTH_FAILURE_CODES, AttemptType, Channel, OutreachStatus, SenderStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import DomainEvent, EventPublisher
from app.ports.providers import ProviderSendResult


class PostSendResolver:
    """Resolves the provider outcome into persisted state and events."""

    def __init__(self, rate_limiter: RateLimiter, event_publisher: EventPublisher) -> None:
        self.rate_limiter = rate_limiter
        self.event_publisher = event_publisher

    def resolve(
        self,
        *,
        session: Any,
        repository_factory: Any,
        attempt: OutreachAttempt,
        sender_account: Any,
        campaign_id: Any,
        attempt_type: AttemptType,
        channel: Channel,
        template: MessageTemplate,
        provider_result: ProviderSendResult,
        now: datetime,
    ) -> OutreachAttempt:
        repos = repository_factory(session)
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
                timestamp=now,
            )
            if db_contact:
                db_contact.record_outreach_success(channel=channel, timestamp=now)
                contact_repo.save(db_contact)

            if db_sender:
                db_sender.record_usage(now)
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
                        "timestamp": now.isoformat(),
                    },
                )
            )

        elif provider_result.status == OutreachStatus.FAILED:
            db_attempt.mark_failed(
                failure_code=provider_result.failure_code or "ERR_PROVIDER_FAILED",
                failure_detail=provider_result.failure_detail or "Delivery failed",
                timestamp=now,
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
                            "timestamp": now.isoformat(),
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
                        "timestamp": now.isoformat(),
                    },
                )
            )

        else:
            # UNKNOWN or RECOVERY_REQUIRED
            if provider_result.status == OutreachStatus.RECOVERY_REQUIRED:
                db_attempt.mark_recovery_required(
                    reason=provider_result.failure_detail or "Ambiguous external outcome",
                    timestamp=now,
                )
            else:
                db_attempt.mark_unknown(
                    reason=provider_result.failure_detail or "Unknown delivery state",
                    timestamp=now,
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
                        "timestamp": now.isoformat(),
                    },
                )
            )

        outreach_repo.save(db_attempt)
        session.commit()
        return db_attempt
