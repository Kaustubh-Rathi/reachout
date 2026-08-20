"""CRM Follow-Up Reminder Policy.

Enforces domain policy for interested candidates:
- Conditions:
  1. `crm_outcome == CRMOutcome.INTERESTED`
  2. `interview_status == InterviewState.PENDING`
  3. Elapsed time since `interested_at` (or `interview_status_changed_at`) >= threshold (default 7 days)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from app.domain.contact import Contact
from app.domain.enums import CRMOutcome, InterviewState
from app.domain.reminder import FollowUpReminder


DEFAULT_FOLLOW_UP_THRESHOLD_DAYS = 7


@dataclass(frozen=True)
class FollowUpEligibility:
    """Detailed evaluation result for a contact's follow-up reminder eligibility."""
    is_due: bool
    contact_id: str
    elapsed_days: float
    reason: str
    due_at: Optional[datetime] = None


def check_contact_follow_up_eligibility(
    contact: Contact,
    current_time: Optional[datetime] = None,
    threshold_days: int = DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
) -> FollowUpEligibility:
    """Evaluate whether a single contact requires a follow-up reminder."""
    now = current_time or datetime.now(timezone.utc)
    
    if contact.crm_outcome != CRMOutcome.INTERESTED:
        return FollowUpEligibility(
            is_due=False,
            contact_id=contact.contact_id,
            elapsed_days=0.0,
            reason="CRM outcome is not INTERESTED",
        )

    if contact.interview_status != InterviewState.PENDING:
        return FollowUpEligibility(
            is_due=False,
            contact_id=contact.contact_id,
            elapsed_days=0.0,
            reason=f"Interview state is {contact.interview_status.value}, not PENDING",
        )

    # Reference milestone is interested_at or interview_status_changed_at or updated_at
    milestone = contact.interested_at or contact.interview_status_changed_at or contact.updated_at
    if not milestone:
        return FollowUpEligibility(
            is_due=False,
            contact_id=contact.contact_id,
            elapsed_days=0.0,
            reason="No interest or milestone timestamp recorded",
        )

    # Normalize timezone awareness to handle SQLite naive datetime values
    if milestone.tzinfo is None and now.tzinfo is not None:
        milestone = milestone.replace(tzinfo=timezone.utc)
    elif milestone.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta = now - milestone
    elapsed_days = max(0.0, delta.total_seconds() / 86400.0)
    due_at = milestone + timedelta(days=threshold_days)

    if elapsed_days >= threshold_days:
        return FollowUpEligibility(
            is_due=True,
            contact_id=contact.contact_id,
            elapsed_days=elapsed_days,
            reason=f"Contact has been INTERESTED with PENDING interview for {elapsed_days:.1f} days (>= {threshold_days} days threshold)",
            due_at=due_at,
        )

    return FollowUpEligibility(
        is_due=False,
        contact_id=contact.contact_id,
        elapsed_days=elapsed_days,
        reason=f"Follow-up not yet due ({elapsed_days:.1f} of {threshold_days} days elapsed)",
        due_at=due_at,
    )


def generate_due_reminders(
    contacts: Sequence[Contact],
    current_time: Optional[datetime] = None,
    threshold_days: int = DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
    existing_reminders: Optional[Sequence[FollowUpReminder]] = None,
) -> List[FollowUpReminder]:
    """Scan contacts and construct FollowUpReminder entities for those due without active reminders."""
    now = current_time or datetime.now(timezone.utc)
    active_reminder_contact_ids = set()
    if existing_reminders:
        active_reminder_contact_ids = {
            r.contact_id for r in existing_reminders if r.is_due(now) or r.status.value == "PENDING"
        }

    reminders: List[FollowUpReminder] = []
    for contact in contacts:
        if contact.contact_id in active_reminder_contact_ids:
            continue
        eval_result = check_contact_follow_up_eligibility(contact, now, threshold_days)
        if eval_result.is_due:
            reminders.append(
                FollowUpReminder.create(
                    contact_id=contact.contact_id,
                    due_at=eval_result.due_at or now,
                    reason=eval_result.reason,
                    created_at=now,
                )
            )

    return reminders
