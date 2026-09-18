"""CRM Outcome & Follow-Up Lifecycle Application Service.

Encapsulates state transitions for:
- Interested / Not Interested
- Interview / Not Interview
- Follow-up reminder evaluation and auto-clearing
- Contact notes
- Dashboard KPI aggregation
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.contact import Contact
from app.domain.enums import CRMOutcome, InterviewState, OutreachStatus, ReminderStatus
from app.domain.policies.reminder_policy import (
    DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
    check_contact_follow_up_eligibility,
)
from app.domain.reminder import FollowUpReminder
from app.services.context import ServiceContext, build_service_context

CrmTransition = Callable[["CrmService", Contact, datetime], None]


def _apply_interested(_: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_crm_outcome(CRMOutcome.INTERESTED, now)


def _apply_not_interested(service: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_crm_outcome(CRMOutcome.NOT_INTERESTED, now)
    service._complete_pending_reminders(contact.contact_id, now)


def _apply_do_not_contact(service: "CrmService", contact: Contact, now: datetime) -> None:
    if "do_not_contact" not in contact.tags:
        contact.tags.append("do_not_contact")
    contact.update_crm_outcome(CRMOutcome.NOT_INTERESTED, now)
    service._complete_pending_reminders(contact.contact_id, now)


def _apply_interview(service: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_crm_outcome(CRMOutcome.INTERESTED, now)
    contact.update_interview_status(InterviewState.INTERVIEW, now)
    service._complete_pending_reminders(contact.contact_id, now)


def _apply_offer(_: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_crm_outcome(CRMOutcome.OFFER, now)
    contact.update_interview_status(InterviewState.INTERVIEW, now)


def _apply_closed(_: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_crm_outcome(CRMOutcome.CLOSED, now)


def _apply_rejected(service: "CrmService", contact: Contact, now: datetime) -> None:
    contact.update_interview_status(InterviewState.NOT_INTERVIEW, now)
    service._complete_pending_reminders(contact.contact_id, now)


# Extend by registering a new handler rather than editing update_status.
CRM_STATUS_TRANSITIONS: Dict[str, CrmTransition] = {
    "INTERESTED": _apply_interested,
    "NOT_INTERESTED": _apply_not_interested,
    "DO_NOT_CONTACT": _apply_do_not_contact,
    "INTERVIEW": _apply_interview,
    "OFFER": _apply_offer,
    "CLOSED": _apply_closed,
    "REJECTED": _apply_rejected,
}


class CrmService:
    """Application service for CRM lifecycle and follow-up reminders."""

    def __init__(self, session: Session, context: Optional[ServiceContext] = None) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.contact_repo = ctx.contact_repo
        self.outreach_repo = ctx.outreach_repo
        self.reminder_repo = ctx.reminder_repo
        self.event_publisher = ctx.event_publisher
        self.clock = ctx.clock

    def has_any_contacts(self) -> bool:
        """Return whether any contact exists (used to decide first-run auto-sync)."""
        return bool(self.contact_repo.list_all())

    def update_status(self, contact_id: str, status: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """Update contact CRM business status directly.

        All transitions are applied to the single loaded aggregate and persisted
        once, so side-effects (tags, interview state, interested_at) are never
        discarded by a later re-fetch.
        """
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        now = timestamp or self.clock.now()
        status_upper = status.strip().upper()

        transition = CRM_STATUS_TRANSITIONS.get(status_upper)
        if transition is not None:
            transition(self, contact, now)
        elif status_upper in CRMOutcome.__members__:
            contact.update_crm_outcome(CRMOutcome(status_upper), now)
        else:
            raise ValueError(f"Invalid status: {status}")

        self.contact_repo.save(contact)
        self.session.commit()

        self._publish_status_events(contact, now)
        return {
            "contact_id": contact.contact_id,
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
        }

    def _complete_pending_reminders(self, contact_id: str, now: datetime) -> None:
        """Mark any pending follow-up reminders for a contact as completed."""
        for r in self.reminder_repo.list_by_contact(contact_id):
            if r.status == ReminderStatus.PENDING:
                r.complete(now)
                self.reminder_repo.save(r)

    def _publish_status_events(self, contact, now: datetime) -> None:
        """Emit the canonical CRM status events for a persisted contact."""
        event_payload = {
            "contact_id": contact.contact_id,
            "name": contact.name,
            "company": contact.company_id,
            "status": contact.crm_outcome.value,
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
            "timestamp": now.isoformat(),
        }
        self.event_publisher.publish_event("CRM_STATUS_CHANGED", event_payload)

    def mark_interested(self, contact_id: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """Mark contact as interested, setting interested_at and resetting interview to PENDING."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        now = timestamp or self.clock.now()
        contact.update_crm_outcome(CRMOutcome.INTERESTED, now)
        self.contact_repo.save(contact)
        self.session.commit()

        # Check and publish event
        self.event_publisher.publish_event(
            "CRM_OUTCOME_UPDATED",
            {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "company": contact.company_id,
                "outcome": "INTERESTED",
                "interested_at": contact.interested_at.isoformat() if contact.interested_at else None,
            },
        )

        return {
            "contact_id": contact.contact_id,
            "crm_outcome": contact.crm_outcome.value,
            "interested_at": contact.interested_at.isoformat() if contact.interested_at else None,
            "interview_status": contact.interview_status.value,
        }

    def mark_not_interested(self, contact_id: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """Mark contact as not interested."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        now = timestamp or self.clock.now()
        contact.update_crm_outcome(CRMOutcome.NOT_INTERESTED, now)
        self.contact_repo.save(contact)

        # Complete/dismiss any pending reminders
        self._complete_pending_reminders(contact_id, now)

        self.session.commit()

        self.event_publisher.publish_event(
            "CRM_OUTCOME_UPDATED",
            {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "company": contact.company_id,
                "outcome": "NOT_INTERESTED",
            },
        )

        return {
            "contact_id": contact.contact_id,
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
        }

    def mark_interview(self, contact_id: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """Mark contact as interview scheduled/progressing. Clears 7-day follow-up reminder."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        now = timestamp or self.clock.now()
        contact.update_interview_status(InterviewState.INTERVIEW, now)
        self.contact_repo.save(contact)

        # Clear any active follow-up reminders
        self._complete_pending_reminders(contact_id, now)

        self.session.commit()

        self.event_publisher.publish_event(
            "INTERVIEW_STATUS_UPDATED",
            {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "company": contact.company_id,
                "interview_status": "INTERVIEW",
            },
        )

        return {
            "contact_id": contact.contact_id,
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
        }

    def mark_not_interview(self, contact_id: str, timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        """Mark contact as not interviewing / rejected. Clears 7-day follow-up reminder."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        now = timestamp or self.clock.now()
        contact.update_interview_status(InterviewState.NOT_INTERVIEW, now)
        self.contact_repo.save(contact)

        # Clear any active follow-up reminders
        self._complete_pending_reminders(contact_id, now)

        self.session.commit()

        self.event_publisher.publish_event(
            "INTERVIEW_STATUS_UPDATED",
            {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "company": contact.company_id,
                "interview_status": "NOT_INTERVIEW",
            },
        )

        return {
            "contact_id": contact.contact_id,
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
        }

    def update_notes(self, contact_id: str, notes: str) -> Dict[str, Any]:
        """Update user conversation notes for contact."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            raise ValueError(f"Contact not found: {contact_id}")

        contact.notes = notes
        contact.updated_at = self.clock.now()
        self.contact_repo.save(contact)
        self.session.commit()
        return {
            "contact_id": contact.contact_id,
            "notes": contact.notes,
            "updated_at": contact.updated_at.isoformat(),
        }

    def list_reminders(self, only_due: bool = False, current_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """List follow-up reminders with associated contact metadata."""
        now = current_time or self.clock.now()
        if only_due:
            reminders = self.reminder_repo.list_due(now)
        else:
            reminders = self.reminder_repo.list_due(now + timedelta(days=365))  # all pending

        results = []
        for r in reminders:
            cnt = self.contact_repo.get_by_id(r.contact_id)
            results.append(
                {
                    "id": r.id,
                    "contact_id": r.contact_id,
                    "contact_name": cnt.name if cnt else "Unknown",
                    "company": cnt.company_id if cnt else "Unknown",
                    "phone": cnt.phone if cnt else None,
                    "email": cnt.email if cnt else None,
                    "due_at": r.due_at.isoformat(),
                    "is_due": r.is_due(now),
                    "reason": r.reason,
                    "status": r.status.value,
                    "created_at": r.created_at.isoformat(),
                }
            )
        return results

    def generate_due_reminders(
        self, threshold_days: int = DEFAULT_FOLLOW_UP_THRESHOLD_DAYS, current_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Scan contacts, identify pending follow-up conditions, and persist reminder entities."""
        now = current_time or self.clock.now()
        contacts = self.contact_repo.list_all()
        created_reminders = []

        for contact in contacts:
            eligibility = check_contact_follow_up_eligibility(contact, now, threshold_days)
            if eligibility.is_due:
                # Check if an active reminder already exists
                existing = self.reminder_repo.list_by_contact(contact.contact_id)
                has_active = any(r.status == ReminderStatus.PENDING for r in existing)
                if not has_active:
                    reminder = FollowUpReminder.create(
                        contact_id=contact.contact_id,
                        due_at=eligibility.due_at or now,
                        reason=eligibility.reason,
                        created_at=now,
                    )
                    self.reminder_repo.save(reminder)
                    created_reminders.append(reminder)
                    self.event_publisher.publish_event(
                        "FOLLOW_UP_DUE",
                        {
                            "contact_id": contact.contact_id,
                            "name": contact.name,
                            "company": contact.company_id,
                            "due_at": reminder.due_at.isoformat(),
                            "reason": reminder.reason,
                        },
                    )

        self.session.commit()
        return [{"id": r.id, "contact_id": r.contact_id, "due_at": r.due_at.isoformat()} for r in created_reminders]

    def get_kpis(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """Aggregate system-wide KPI metrics."""
        now = current_time or self.clock.now()
        contacts = self.contact_repo.list_all()
        total_contacts = len(contacts)

        contacted = 0
        wa_sent = 0
        email_sent = 0
        interested = 0
        not_interested = 0
        interview = 0
        follow_up_due = 0
        eligible = 0

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

            # Follow-up due calculation
            eligibility = check_contact_follow_up_eligibility(c, now, DEFAULT_FOLLOW_UP_THRESHOLD_DAYS)
            if eligibility.is_due:
                follow_up_due += 1

            # Eligible for initial outreach (uncontacted on at least one channel)
            if not has_wa and c.phone:
                eligible += 1
            elif not has_email and c.email:
                eligible += 1

        failed_attempts = len(self.outreach_repo.list_by_status(OutreachStatus.FAILED))
        recovery_required = len(self.outreach_repo.list_by_status(OutreachStatus.RECOVERY_REQUIRED)) + len(
            self.outreach_repo.list_by_status(OutreachStatus.UNKNOWN)
        )

        return {
            "total_contacts": total_contacts,
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
