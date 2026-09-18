"""CRM KPI aggregation.

Computes dashboard KPI metrics from contacts and outreach attempts. Kept
separate from CRM command handling (status transitions, reminders, notes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.domain.enums import CRMOutcome, InterviewState, OutreachStatus
from app.domain.policies.reminder_policy import (
    DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
    check_contact_follow_up_eligibility,
)
from app.ports.infrastructure import Clock
from app.ports.repositories import ContactRepository, OutreachRepository


class CrmAnalyticsService:
    """Aggregates system-wide CRM KPI metrics."""

    def __init__(
        self,
        contact_repo: ContactRepository,
        outreach_repo: OutreachRepository,
        clock: Clock,
    ) -> None:
        self.contact_repo = contact_repo
        self.outreach_repo = outreach_repo
        self.clock = clock

    def get_kpis(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        now = current_time or self.clock.now()
        contacts = self.contact_repo.list_all()

        contacted = wa_sent = email_sent = 0
        interested = not_interested = interview = follow_up_due = eligible = 0

        for c in contacts:
            has_wa = c.last_whatsapp_at is not None
            has_email = c.last_email_at is not None
            if has_wa or has_email:
                contacted += 1
            if has_wa:
                wa_sent += 1
            if has_email:
                email_sent += 1

            if c.crm_outcome == CRMOutcome.INTERESTED:
                interested += 1
            elif c.crm_outcome == CRMOutcome.NOT_INTERESTED:
                not_interested += 1

            if c.interview_status == InterviewState.INTERVIEW:
                interview += 1

            if check_contact_follow_up_eligibility(c, now, DEFAULT_FOLLOW_UP_THRESHOLD_DAYS).is_due:
                follow_up_due += 1

            if not has_wa and c.phone:
                eligible += 1
            elif not has_email and c.email:
                eligible += 1

        failed_attempts = len(self.outreach_repo.list_by_status(OutreachStatus.FAILED))
        recovery_required = len(self.outreach_repo.list_by_status(OutreachStatus.RECOVERY_REQUIRED)) + len(
            self.outreach_repo.list_by_status(OutreachStatus.UNKNOWN)
        )

        return {
            "total_contacts": len(contacts),
            "eligible": eligible,
            "contacted": contacted,
            "whatsapp_sent": wa_sent,
            "email_sent": email_sent,
            "interested": interested,
            "not_interested": not_interested,
            "interview": interview,
            "follow_up_due": follow_up_due,
            "failed": failed_attempts,
            "recovery_required": recovery_required,
        }
