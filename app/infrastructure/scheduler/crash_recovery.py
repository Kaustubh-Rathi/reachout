"""Crash recovery audit for in-flight outreach attempts.

Scans for orphaned attempts left in SENDING/QUEUED after a process restart and
auto-pauses campaigns that were running, so operators can review them.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import CampaignStatus, OutreachStatus
from app.ports.infrastructure import Clock, DomainEvent, EventPublisher


class CrashRecoveryService:
    """Recovers stale in-flight attempts and auto-pauses interrupted campaigns."""

    def __init__(
        self,
        session_factory: Any,
        repository_factory: Any,
        event_publisher: EventPublisher,
        clock: Clock,
    ) -> None:
        self.session_factory = session_factory
        self.repository_factory = repository_factory
        self.event_publisher = event_publisher
        self.clock = clock

    def run(self) -> int:
        recovered_count = 0
        now = self.clock.now()
        with self.session_factory() as session:
            repos = self.repository_factory(session)
            outreach_repo = repos.outreach
            campaign_repo = repos.campaign

            for status in (OutreachStatus.SENDING, OutreachStatus.QUEUED):
                for attempt in outreach_repo.list_by_status(status):
                    attempt.mark_recovery_required(
                        reason="Process crash recovery: attempt was in-flight when server restarted",
                        timestamp=now,
                    )
                    attempt.recovery_notes = f"Auto-flagged by crash recovery audit at {now.isoformat()}"
                    outreach_repo.save(attempt)
                    recovered_count += 1
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="ATTEMPT_RECOVERY_REQUIRED",
                            payload={"attempt_id": attempt.id, "reason": attempt.failure_detail},
                        )
                    )

            for cmp in campaign_repo.list_all():
                if cmp.status in (CampaignStatus.RUNNING, CampaignStatus.STARTING):
                    cmp.pause()
                    campaign_repo.save(cmp)
                    self.event_publisher.publish(
                        DomainEvent(
                            event_type="CAMPAIGN_PAUSED",
                            payload={"campaign_id": cmp.id, "reason": "Crash recovery auto-pause"},
                        )
                    )

            session.commit()

        return recovered_count
