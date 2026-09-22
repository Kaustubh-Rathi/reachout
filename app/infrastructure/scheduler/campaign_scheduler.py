"""Persistent Campaign Scheduler and Execution Manager.

Manages background campaign lifecycle (START, PAUSE, RESUME), contact
selection with company-first prioritization, deterministic template rotation,
N-scale sender distribution, quota enforcement, channel fallback, and crash recovery.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from app.domain.enums import AttemptType, CampaignStatus, Channel, OutreachStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.policies.channel_rotation_policy import (
    ChannelRotationPolicy,
)
from app.domain.policies.endpoint_coverage_policy import (
    is_contact_fully_covered,
)
from app.domain.policies.sender_rotation import SenderRotationPolicy
from app.domain.policies.template_rotation import select_template_round_robin
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.providers.attachments import resolve_attachment_path
from app.infrastructure.providers.factory import get_email_provider, get_whatsapp_provider
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.candidate_selector import CandidateSelector
from app.infrastructure.scheduler.crash_recovery import CrashRecoveryService
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import CampaignScheduler, Clock, DomainEvent, EventPublisher

logger = logging.getLogger(__name__)


class PersistentCampaignScheduler(CampaignScheduler):
    """Persistent scheduler executing campaigns against database state in the background."""

    def __init__(
        self,
        session_factory: Any,
        worker: OutreachWorker,
        event_publisher: EventPublisher,
        repository_factory: Any,
        clock: Clock,
    ) -> None:
        self.session_factory = session_factory
        self.worker = worker
        self.event_publisher = event_publisher
        self.repository_factory = repository_factory
        self.clock = clock
        self.crash_recovery = CrashRecoveryService(
            session_factory=session_factory,
            repository_factory=repository_factory,
            event_publisher=event_publisher,
            clock=clock,
        )
        self._lock = threading.RLock()
        self._active_threads: Dict[str, threading.Thread] = {}
        self._pause_flags: Dict[str, threading.Event] = {}

    def is_running(self, campaign_id: str) -> bool:
        """Check if background worker thread is actively executing for campaign."""
        with self._lock:
            t = self._active_threads.get(campaign_id)
            return t is not None and t.is_alive()

    def run_crash_recovery_audit(self) -> int:
        """Scan for orphaned in-flight attempts after a restart and recover them."""
        return self.crash_recovery.run()

    def _validate_template_attachments(self, session, campaign, operation: str) -> None:
        """Refuse to run when a template attachment cannot be resolved.

        Raises ValidationError before any worker spawns or status flips, so a
        bad attachment path surfaces once instead of failing every candidate
        in the run. The campaign keeps its status and can start/resume after
        the template path is fixed.
        """
        template_repo = self.repository_factory(session).template
        templates = template_repo.list_by_channel(campaign.channel, active_only=True)
        if not templates:
            templates = template_repo.list_by_channel(campaign.channel, active_only=False)
        offenders = []
        for tmpl in templates:
            ref = tmpl.attachment_ref
            if ref:
                resolved = resolve_attachment_path(ref)
                if resolved is None or not resolved.exists():
                    offenders.append(f"{tmpl.id} ({ref})")
        if offenders:
            raise ValidationError(
                f"Cannot {operation} campaign '{campaign.id}': "
                f"template attachment(s) not found: {', '.join(offenders)}. "
                "Fix the template attachment path and retry."
            )

    def start_campaign(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Start execution of a campaign in the background."""
        with self._lock:
            with self.session_factory() as session:
                campaign_repo = self.repository_factory(session).campaign
                campaign = campaign_repo.get_by_id(campaign_id)
                if not campaign:
                    raise NotFoundError(f"Campaign '{campaign_id}' not found")

                if self.is_running(campaign_id):
                    return

                self._validate_template_attachments(session, campaign, operation="start")

                if campaign.status == CampaignStatus.PAUSED:
                    self.resume_campaign(campaign_id)
                    return

                if campaign.status not in (CampaignStatus.IDLE, CampaignStatus.STARTING, CampaignStatus.RUNNING):
                    raise ValidationError(f"Cannot start campaign in status '{campaign.status.value}'")

                if campaign.status in (CampaignStatus.IDLE, CampaignStatus.STARTING):
                    campaign.start()
                    campaign_repo.save(campaign)
                    session.commit()

            self._pause_flags[campaign_id] = threading.Event()

            t = threading.Thread(
                target=self._run_campaign_loop,
                args=(campaign_id, max_count),
                daemon=True,
                name=f"campaign-worker-{campaign_id}",
            )
            self._active_threads[campaign_id] = t
            t.start()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="CAMPAIGN_STARTED",
                    payload={"campaign_id": campaign_id, "channel": campaign.channel.value},
                )
            )

    def pause_campaign(self, campaign_id: str) -> None:
        """Pause a running campaign."""
        with self._lock:
            if campaign_id in self._pause_flags:
                self._pause_flags[campaign_id].set()

            with self.session_factory() as session:
                campaign_repo = self.repository_factory(session).campaign
                campaign = campaign_repo.get_by_id(campaign_id)
                if campaign and campaign.status in (CampaignStatus.STARTING, CampaignStatus.RUNNING):
                    campaign_repo.set_status(campaign_id, CampaignStatus.PAUSED)
                    session.commit()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="CAMPAIGN_PAUSED",
                    payload={"campaign_id": campaign_id},
                )
            )

    def resume_campaign(self, campaign_id: str) -> None:
        """Resume a paused campaign."""
        with self._lock:
            with self.session_factory() as session:
                campaign_repo = self.repository_factory(session).campaign
                campaign = campaign_repo.get_by_id(campaign_id)
                if not campaign:
                    raise NotFoundError(f"Campaign '{campaign_id}' not found")

                self._validate_template_attachments(session, campaign, operation="resume")

                if campaign.status == CampaignStatus.PAUSED:
                    campaign_repo.set_status(campaign_id, CampaignStatus.RUNNING)
                    session.commit()

            t = self._active_threads.get(campaign_id)
            if t is not None and t.is_alive():
                if campaign_id in self._pause_flags:
                    self._pause_flags[campaign_id].clear()
            else:
                self._pause_flags[campaign_id] = threading.Event()
                new_t = threading.Thread(
                    target=self._run_campaign_loop,
                    args=(campaign_id, None),
                    daemon=True,
                    name=f"campaign-worker-{campaign_id}",
                )
                self._active_threads[campaign_id] = new_t
                new_t.start()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="CAMPAIGN_STARTED",
                    payload={"campaign_id": campaign_id, "channel": campaign.channel.value},
                )
            )

    def _run_campaign_loop(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Background loop executing campaign attempts using OutreachWorker and RateLimiter."""
        pause_flag = self._pause_flags.get(campaign_id)
        dispatched_count = 0

        while True:
            if pause_flag and pause_flag.is_set():
                time.sleep(0.1)
                continue

            # 1. Target max count check
            if max_count is not None and dispatched_count >= max_count:
                with self.session_factory() as session:
                    campaign_repo = self.repository_factory(session).campaign
                    campaign = campaign_repo.get_by_id(campaign_id)
                    if campaign and campaign.status == CampaignStatus.RUNNING:
                        campaign.complete()
                        campaign_repo.save(campaign)
                        session.commit()
                        self.event_publisher.publish(
                            DomainEvent(
                                event_type="CAMPAIGN_COMPLETED",
                                payload={"campaign_id": campaign.id, "reason": "Max count reached"},
                            )
                        )
                break

            # 2. Recompute eligible candidate list and senders from fresh database state
            with self.session_factory() as session:
                repos = self.repository_factory(session)
                campaign_repo = repos.campaign
                contact_repo = repos.contact
                outreach_repo = repos.outreach
                template_repo = repos.template
                sender_repo = repos.sender
                suppression_repo = repos.suppression

                campaign = campaign_repo.get_by_id(campaign_id)
                if not campaign or campaign.status.is_terminal:
                    break

                if campaign.status == CampaignStatus.PAUSED:
                    time.sleep(0.1)
                    continue

                # Check Campaign Quota limit
                if not campaign.can_dispatch_automatic():
                    campaign.complete()
                    campaign_repo.save(campaign)
                    session.commit()
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="CAMPAIGN_COMPLETED",
                            payload={"campaign_id": campaign.id, "reason": "Automatic quota reached"},
                        )
                    )
                    break

                # Load persistent rotation state from campaign metadata
                rot_state = dict(campaign.metadata.get("rotation_state", {}))
                channel_cursor = int(rot_state.get("channel_cursor", 0))
                sender_cursors = dict(rot_state.get("sender_cursors", {"WHATSAPP": 0, "EMAIL": 0}))
                template_cursors = dict(rot_state.get("template_cursors", {"WHATSAPP": 0, "EMAIL": 0}))

                suppressed_set = {s.identifier for s in suppression_repo.list_all()}

                # Resolve all active sender accounts across both channels
                active_wa_senders = sender_repo.list_active(channel=Channel.WHATSAPP)
                active_em_senders = sender_repo.list_active(channel=Channel.EMAIL)
                all_active_senders = active_wa_senders + active_em_senders

                if not all_active_senders:
                    campaign.fail(reason="No active sender accounts available across any channel")
                    campaign_repo.save(campaign)
                    session.commit()
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="CAMPAIGN_FAILED",
                            payload={"campaign_id": campaign.id, "reason": "No active senders"},
                        )
                    )
                    break

                # Build dynamic channel rotation sequence
                rotation_sequence = ChannelRotationPolicy.build_dynamic_channel_sequence(all_active_senders)
                preferred_channel, next_channel_cursor = ChannelRotationPolicy.get_preferred_channel(
                    cursor=channel_cursor,
                    sequence=rotation_sequence,
                )

                # Load all contacts and full historical attempts
                all_contacts = contact_repo.list_all()
                all_attempts = outreach_repo.list_all()
                attempts_by_contact = CandidateSelector.attempts_by_contact(all_attempts)

                # Prioritize WHO (Company-First Round-Robin selection).
                prioritized_candidates = CandidateSelector.select(
                    contacts=all_contacts,
                    attempts=all_attempts,
                    suppressed_identifiers=suppressed_set,
                    preferred_channel=preferred_channel,
                )

                if not prioritized_candidates:
                    # Check if ANY contact has remaining uncovered endpoints
                    any_remaining = False
                    for c in all_contacts:
                        c_hist = attempts_by_contact.get(c.contact_id, [])
                        if not is_contact_fully_covered(c, c_hist, suppressed_set):
                            any_remaining = True
                            break

                    if not any_remaining:
                        # All eligible contacts and endpoints are fully covered!
                        campaign.complete()
                        campaign_repo.save(campaign)
                        session.commit()
                        self.event_publisher.publish(
                            DomainEvent(
                                event_type="CAMPAIGN_COMPLETED",
                                payload={"campaign_id": campaign.id},
                            )
                        )
                        break
                    else:
                        # Advance channel cursor if preferred channel has no targets in this step
                        rot_state["channel_cursor"] = next_channel_cursor
                        campaign_repo.set_rotation_state(campaign_id, rot_state)
                        session.commit()
                        time.sleep(0.2)
                        continue

                next_contact = prioritized_candidates[0]
                contact_hist = attempts_by_contact.get(next_contact.contact_id, [])

                # Evaluate effective channel and exact target endpoint
                decision = ChannelRotationPolicy.evaluate_contact_dispatch(
                    contact=next_contact,
                    preferred_channel=preferred_channel,
                    historical_attempts=contact_hist,
                    suppressed_identifiers=suppressed_set,
                )

                if not decision.is_eligible or decision.channel is None:
                    # Contact not eligible for dispatch; advance rotation
                    rot_state["channel_cursor"] = next_channel_cursor
                    campaign_repo.set_rotation_state(campaign_id, rot_state)
                    session.commit()
                    time.sleep(0.1)
                    continue

                effective_channel = decision.channel
                target_endpoint = decision.endpoint
                target_destination = target_endpoint.normalized_address if target_endpoint else None

                # Resolve active message templates for effective channel
                channel_templates = template_repo.list_by_channel(effective_channel, active_only=True)
                if not channel_templates:
                    channel_templates = template_repo.list_by_channel(effective_channel, active_only=False)

                if not channel_templates:
                    campaign.fail(reason=f"No message templates found for channel {effective_channel.value}")
                    campaign_repo.save(campaign)
                    session.commit()
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="CAMPAIGN_FAILED",
                            payload={
                                "campaign_id": campaign.id,
                                "reason": f"Missing {effective_channel.value} templates",
                            },
                        )
                    )
                    break

                # Resolve active senders for effective channel
                channel_senders = [s for s in all_active_senders if s.channel == effective_channel and s.is_available()]
                if not channel_senders:
                    channel_senders = sender_repo.list_active(channel=effective_channel)

                if not channel_senders:
                    # No ACTIVE sender exists for this channel. Surface it once per
                    # channel instead of silently skipping, so a dead session is visible.
                    reported = set(rot_state.get("reported_unavailable_channels", []))
                    if effective_channel.value not in reported:
                        reported.add(effective_channel.value)
                        rot_state["reported_unavailable_channels"] = sorted(reported)
                        logger.warning(
                            "No ACTIVE sender for channel %s in campaign %s",
                            effective_channel.value,
                            campaign_id,
                        )
                        self.event_publisher.publish(
                            DomainEvent(
                                event_type="CAMPAIGN_SENDER_UNAVAILABLE",
                                payload={
                                    "campaign_id": campaign_id,
                                    "channel": effective_channel.value,
                                    "reason": "NO_ACTIVE_SENDER_FOR_CHANNEL",
                                },
                            )
                        )
                    rot_state["channel_cursor"] = next_channel_cursor
                    campaign_repo.set_rotation_state(campaign_id, rot_state)
                    session.commit()
                    time.sleep(0.2)
                    continue

                # Template selection (persistent deterministic round-robin per channel)
                tmpl_cursor = int(template_cursors.get(effective_channel.value, 0))
                selected_template = select_template_round_robin(channel_templates, tmpl_cursor)
                template_cursors[effective_channel.value] = tmpl_cursor + 1

                # Sender selection (persistent deterministic round-robin per channel, rate-limiter aware)
                snd_cursor = int(sender_cursors.get(effective_channel.value, 0))

                def _sender_ready(s: SenderAccount) -> bool:
                    if not s.is_available():
                        return False
                    can, _ = self.worker.rate_limiter.can_send(
                        sender_id=s.id,
                        channel=s.channel.value,
                        daily_limit=s.daily_limit,
                        hourly_limit=s.hourly_limit,
                    )
                    return can

                selected_sender, new_snd_cursor = SenderRotationPolicy.select_next_sender(
                    senders=channel_senders,
                    cursor=snd_cursor,
                    availability_checker=_sender_ready,
                )

                if not selected_sender:
                    # All senders for this channel temporarily unavailable (cooling down or busy)
                    time.sleep(0.02)
                    continue

                sender_cursors[effective_channel.value] = new_snd_cursor

                # Advance channel rotation cursor
                channel_cursor = next_channel_cursor

                # Persist updated rotation state to metadata. set_rotation_state only
                # writes the rotation_state key, so it cannot clobber the worker's
                # quota counters, and it never rewrites status — pause stays authoritative.
                rot_state["channel_cursor"] = channel_cursor
                rot_state["sender_cursors"] = sender_cursors
                rot_state["template_cursors"] = template_cursors
                campaign_repo.set_rotation_state(campaign_id, rot_state)
                session.commit()

                # If a pause landed while computing, do not dispatch this step.
                # Resuming is an explicit operator (UI) action only.
                if pause_flag and pause_flag.is_set():
                    continue

                contact_id = next_contact.contact_id

            # Dispatch attempt using OutreachWorker outside session lock
            try:
                attempt = self.worker.execute_attempt(
                    contact_id=contact_id,
                    sender_account=selected_sender,
                    template=selected_template,
                    campaign=campaign,
                    attempt_type=AttemptType.AUTOMATIC,
                    destination=target_destination,
                )
                if attempt.status == OutreachStatus.SENT:
                    dispatched_count += 1
            except Exception as exc:
                # Surface attempt-execution failures instead of silently skipping the
                # contact. Mark the attempt FAILED (if one was created) so it is visible
                # in the audit trail and does not linger in an in-flight state.
                logger.exception("Attempt error for contact %s", contact_id)
                try:
                    with self.session_factory() as sess:
                        orep = self.repository_factory(sess).outreach
                        # Find any PREPARED/SENDING attempt for this contact+channel
                        target_channel = selected_template.channel if selected_template else None
                        for a in orep.list_by_contact(contact_id):
                            if a.channel == target_channel and a.status in (
                                OutreachStatus.PREPARED,
                                OutreachStatus.SENDING,
                            ):
                                a.mark_failed("ERR_WORKER_EXCEPTION", str(exc), self.clock.now())
                                orep.save(a)
                        sess.commit()
                except Exception:
                    logger.exception("Failed to record attempt failure for contact %s", contact_id)
                time.sleep(1.0)

        with self._lock:
            self._active_threads.pop(campaign_id, None)
            self._pause_flags.pop(campaign_id, None)


# Canonical singleton instance & registry
_campaign_scheduler_instance: Optional[PersistentCampaignScheduler] = None


def get_campaign_scheduler(
    session_factory: Any = None,
    worker: Optional[OutreachWorker] = None,
    event_publisher: Optional[EventPublisher] = None,
    rate_limiter: Optional[RateLimiter] = None,
) -> PersistentCampaignScheduler:
    """Get or create the canonical PersistentCampaignScheduler instance."""
    global _campaign_scheduler_instance
    if _campaign_scheduler_instance is None:
        from app.composition import build_repositories, get_event_bus, get_rate_limiter
        from app.ports.infrastructure import SystemClock

        sf = session_factory or SessionFactory
        bus = event_publisher or get_event_bus()
        limiter = rate_limiter or get_rate_limiter()
        clock = SystemClock()
        w = worker or OutreachWorker(
            session_factory=sf,
            whatsapp_provider=get_whatsapp_provider(),
            email_provider=get_email_provider(),
            rate_limiter=limiter,
            event_publisher=bus,
            repository_factory=build_repositories,
            clock=clock,
        )
        _campaign_scheduler_instance = PersistentCampaignScheduler(
            session_factory=sf,
            worker=w,
            event_publisher=bus,
            repository_factory=build_repositories,
            clock=clock,
        )
    return _campaign_scheduler_instance


def set_campaign_scheduler(scheduler: Optional[PersistentCampaignScheduler]) -> None:
    """Explicitly set the canonical campaign scheduler instance (for tests)."""
    global _campaign_scheduler_instance
    _campaign_scheduler_instance = scheduler


def reset_campaign_scheduler() -> None:
    """Reset the canonical scheduler singleton."""
    global _campaign_scheduler_instance
    _campaign_scheduler_instance = None
