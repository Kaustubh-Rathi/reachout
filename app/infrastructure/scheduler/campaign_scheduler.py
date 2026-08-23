"""Persistent Campaign Scheduler and Execution Manager.

Manages background campaign lifecycle (START, PAUSE, RESUME, STOP), contact
selection with company-first prioritization, deterministic template rotation,
N-scale sender distribution, quota enforcement, channel fallback, and crash recovery.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.campaign import Campaign
from app.domain.contact import Contact
from app.domain.enums import AttemptType, CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.message_template import MessageTemplate
from app.domain.policies.channel_rotation_policy import (
    ChannelDispatchDecision,
    ChannelRotationPolicy,
)
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility
from app.domain.policies.endpoint_coverage_policy import (
    get_next_uncovered_endpoint,
    get_uncovered_endpoints,
    is_contact_fully_covered,
    is_endpoint_covered,
)
from app.domain.policies.fallback_policy import ChannelFallbackPolicy
from app.domain.policies.prioritization import (
    CompanyRoundMetrics,
    ContactPrioritizer,
    calculate_company_round_state,
    prioritize_company_first,
)
from app.domain.policies.sender_rotation import SenderRotationPolicy
from app.domain.policies.template_rotation import select_template_round_robin
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.events.event_bus import default_event_bus
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import DomainEvent, EventPublisher, Scheduler


class PersistentCampaignScheduler:
    """Persistent scheduler executing campaigns against database state in the background."""

    def __init__(
        self,
        session_factory: Any = None,
        worker: Optional[OutreachWorker] = None,
        event_publisher: Optional[EventPublisher] = None,
    ) -> None:
        self.session_factory = session_factory or SessionFactory
        self.worker = worker or OutreachWorker(session_factory=self.session_factory)
        self.event_publisher = event_publisher or default_event_bus
        self._lock = threading.RLock()
        self._active_threads: Dict[str, threading.Thread] = {}
        self._pause_flags: Dict[str, threading.Event] = {}
        self._stop_flags: Dict[str, threading.Event] = {}

    def is_running(self, campaign_id: str) -> bool:
        """Check if background worker thread is actively executing for campaign."""
        with self._lock:
            t = self._active_threads.get(campaign_id)
            return t is not None and t.is_alive()

    def run_crash_recovery_audit(self) -> int:
        """Scan database for orphaned in-flight attempts following process restart and mark as RECOVERY_REQUIRED."""
        recovered_count = 0
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            outreach_repo = SqliteOutreachRepository(session)
            campaign_repo = SqliteCampaignRepository(session)

            in_flight_statuses = [OutreachStatus.SENDING, OutreachStatus.QUEUED]
            for st in in_flight_statuses:
                stalled_attempts = outreach_repo.list_by_status(st)
                for attempt in stalled_attempts:
                    attempt.mark_recovery_required(
                        reason="Process crash recovery: attempt was in-flight when server restarted",
                        timestamp=now,
                    )
                    attempt.recovery_notes = f"Auto-flagged by crash recovery audit at {now.isoformat()}"
                    outreach_repo.save(attempt)
                    recovered_count += 1
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="AttemptRecoveryRequired",
                            payload={"attempt_id": attempt.id, "reason": attempt.failure_detail},
                        )
                    )

            # Check campaigns in RUNNING/STARTING state and transition to PAUSED
            all_campaigns = campaign_repo.list_all()
            for cmp in all_campaigns:
                if cmp.status in (CampaignStatus.RUNNING, CampaignStatus.STARTING):
                    cmp.pause()
                    campaign_repo.save(cmp)
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="CampaignPaused",
                            payload={"campaign_id": cmp.id, "reason": "Crash recovery auto-pause"},
                        )
                    )

            session.commit()

        return recovered_count

    def start_campaign(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Start execution of a campaign in the background."""
        with self._lock:
            with self.session_factory() as session:
                campaign_repo = SqliteCampaignRepository(session)
                campaign = campaign_repo.get_by_id(campaign_id)
                if not campaign:
                    raise ValueError(f"Campaign '{campaign_id}' not found")

                if self.is_running(campaign_id):
                    return

                if campaign.status == CampaignStatus.PAUSED:
                    self.resume_campaign(campaign_id)
                    return

                if campaign.status not in (CampaignStatus.IDLE, CampaignStatus.STARTING, CampaignStatus.RUNNING):
                    raise ValueError(f"Cannot start campaign in status '{campaign.status.value}'")

                if campaign.status in (CampaignStatus.IDLE, CampaignStatus.STARTING):
                    campaign.start()
                    campaign_repo.save(campaign)
                    session.commit()

            self._pause_flags[campaign_id] = threading.Event()
            self._stop_flags[campaign_id] = threading.Event()

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
                    event_type="CampaignStarted",
                    payload={"campaign_id": campaign_id, "channel": campaign.channel.value},
                )
            )

    def pause_campaign(self, campaign_id: str) -> None:
        """Pause a running campaign."""
        with self._lock:
            if campaign_id in self._pause_flags:
                self._pause_flags[campaign_id].set()

            with self.session_factory() as session:
                campaign_repo = SqliteCampaignRepository(session)
                campaign = campaign_repo.get_by_id(campaign_id)
                if campaign and campaign.status in (CampaignStatus.STARTING, CampaignStatus.RUNNING):
                    campaign_repo.set_status(campaign_id, CampaignStatus.PAUSED)
                    session.commit()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="CampaignPaused",
                    payload={"campaign_id": campaign_id},
                )
            )

    def resume_campaign(self, campaign_id: str) -> None:
        """Resume a paused campaign."""
        with self._lock:
            with self.session_factory() as session:
                campaign_repo = SqliteCampaignRepository(session)
                campaign = campaign_repo.get_by_id(campaign_id)
                if not campaign:
                    raise ValueError(f"Campaign '{campaign_id}' not found")

                if campaign.status == CampaignStatus.PAUSED:
                    campaign_repo.set_status(campaign_id, CampaignStatus.RUNNING)
                    session.commit()

            t = self._active_threads.get(campaign_id)
            if t is not None and t.is_alive():
                if campaign_id in self._pause_flags:
                    self._pause_flags[campaign_id].clear()
            else:
                self._pause_flags[campaign_id] = threading.Event()
                self._stop_flags[campaign_id] = threading.Event()
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
                    event_type="CampaignStarted",
                    payload={"campaign_id": campaign_id, "channel": campaign.channel.value},
                )
            )

    def stop_campaign(self, campaign_id: str) -> None:
        """Stop an active or paused campaign permanently."""
        with self._lock:
            if campaign_id in self._stop_flags:
                self._stop_flags[campaign_id].set()
            if campaign_id in self._pause_flags:
                self._pause_flags[campaign_id].clear()

            with self.session_factory() as session:
                campaign_repo = SqliteCampaignRepository(session)
                campaign = campaign_repo.get_by_id(campaign_id)
                if campaign and not campaign.status.is_terminal:
                    campaign_repo.set_status(
                        campaign_id,
                        CampaignStatus.STOPPED,
                        ended_at=datetime.now(timezone.utc),
                    )
                    session.commit()

            self.event_publisher.publish(
                DomainEvent(
                    event_type="CampaignStopped",
                    payload={"campaign_id": campaign_id},
                )
            )

    def _run_campaign_loop(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Background loop executing campaign attempts using OutreachWorker and RateLimiter."""
        stop_flag = self._stop_flags.get(campaign_id)
        pause_flag = self._pause_flags.get(campaign_id)
        dispatched_count = 0

        while True:
            if stop_flag and stop_flag.is_set():
                break

            if pause_flag and pause_flag.is_set():
                time.sleep(0.1)
                continue

            # 1. Target max count check
            if max_count is not None and dispatched_count >= max_count:
                with self.session_factory() as session:
                    campaign_repo = SqliteCampaignRepository(session)
                    campaign = campaign_repo.get_by_id(campaign_id)
                    if campaign and campaign.status == CampaignStatus.RUNNING:
                        campaign.complete()
                        campaign_repo.save(campaign)
                        session.commit()
                        self.event_publisher.publish(
                            DomainEvent(
                                event_type="CampaignCompleted",
                                payload={"campaign_id": campaign.id, "reason": "Max count reached"},
                            )
                        )
                break

            # 2. Recompute eligible candidate list and senders from fresh database state
            with self.session_factory() as session:
                campaign_repo = SqliteCampaignRepository(session)
                contact_repo = SqliteContactRepository(session)
                outreach_repo = SqliteOutreachRepository(session)
                template_repo = SqliteTemplateRepository(session)
                sender_repo = SqliteSenderRepository(session)
                suppression_repo = SqliteSuppressionRepository(session)

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
                            event_type="CampaignCompleted",
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
                            event_type="CampaignFailed",
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

                # Build lookup of attempts per contact
                attempts_by_contact: Dict[str, List[OutreachAttempt]] = {}
                for a in all_attempts:
                    if a.contact_id not in attempts_by_contact:
                        attempts_by_contact[a.contact_id] = []
                    attempts_by_contact[a.contact_id].append(a)

                # Prioritize WHO (Company-First Round-Robin selection)
                def eligibility_check(cnt: Contact) -> bool:
                    hist = attempts_by_contact.get(cnt.contact_id, [])
                    decision = ChannelRotationPolicy.evaluate_contact_dispatch(
                        contact=cnt,
                        preferred_channel=preferred_channel,
                        historical_attempts=hist,
                        suppressed_identifiers=suppressed_set,
                    )
                    return decision.is_eligible

                prioritized_candidates = prioritize_company_first(
                    contacts=all_contacts,
                    eligibility_predicate=eligibility_check,
                    dispatched_contact_ids=attempts_by_contact,
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
                                event_type="CampaignCompleted",
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
                            event_type="CampaignFailed",
                            payload={"campaign_id": campaign.id, "reason": f"Missing {effective_channel.value} templates"},
                        )
                    )
                    break

                # Resolve active senders for effective channel
                channel_senders = [s for s in all_active_senders if s.channel == effective_channel and s.is_available()]
                if not channel_senders:
                    channel_senders = sender_repo.list_active(channel=effective_channel)

                if not channel_senders:
                    # Skip contact if no sender available for effective channel
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
                import traceback
                traceback.print_exc()
                print(f"[Scheduler] Attempt error for contact {contact_id}: {exc}")
                try:
                    with self.session_factory() as sess:
                        orep = SqliteOutreachRepository(sess)
                        att = orep.get_by_id(selected_sender.id) if False else None
                        # Find any PREPARED/SENDING attempt for this contact+channel
                        for a in orep.list_by_contact(contact_id):
                            if a.channel == (selected_template.channel if selected_template else None) and                                a.status in (OutreachStatus.PREPARED, OutreachStatus.SENDING):
                                a.mark_failed("ERR_WORKER_EXCEPTION", str(exc), datetime.now(timezone.utc))
                                orep.save(a)
                        sess.commit()
                except Exception as mark_exc:
                    print(f"[Scheduler] Also failed to record attempt failure: {mark_exc}")

        with self._lock:
            self._active_threads.pop(campaign_id, None)
            self._pause_flags.pop(campaign_id, None)
            self._stop_flags.pop(campaign_id, None)


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
        sf = session_factory or SessionFactory
        w = worker or OutreachWorker(
            session_factory=sf,
            rate_limiter=rate_limiter,
            event_publisher=event_publisher,
        )
        _campaign_scheduler_instance = PersistentCampaignScheduler(
            session_factory=sf,
            worker=w,
            event_publisher=event_publisher,
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
