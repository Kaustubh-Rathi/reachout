"""Unit tests for CRM Follow-Up Reminder Policy."""

from datetime import datetime, timedelta, timezone

from app.domain.contact import Contact
from app.domain.enums import CRMOutcome, InterviewState
from app.domain.policies.reminder_policy import (
    check_contact_follow_up_eligibility,
    generate_due_reminders,
)
from app.domain.reminder import FollowUpReminder


class TestFollowUpReminderPolicy:
    def test_interested_and_pending_less_than_7_days_not_due(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c1",
            company_id="google",
            name="Alice",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=t0,
        )
        # 6 days later
        now = t0 + timedelta(days=6)
        result = check_contact_follow_up_eligibility(contact, current_time=now, threshold_days=7)
        assert not result.is_due
        assert "not yet due" in result.reason.lower()

    def test_interested_and_pending_7_or_more_days_is_due(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c2",
            company_id="amazon",
            name="Bob",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=t0,
        )
        # Exactly 7 days later
        now = t0 + timedelta(days=7)
        result = check_contact_follow_up_eligibility(contact, current_time=now, threshold_days=7)
        assert result.is_due
        assert result.due_at == t0 + timedelta(days=7)
        assert "threshold" in result.reason

    def test_already_in_interview_state_is_not_due(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c3",
            company_id="meta",
            name="Charlie",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.INTERVIEW,
            interested_at=t0,
        )
        # 10 days later
        now = t0 + timedelta(days=10)
        result = check_contact_follow_up_eligibility(contact, current_time=now, threshold_days=7)
        assert not result.is_due
        assert "INTERVIEW" in result.reason

    def test_not_interested_outcome_is_not_due(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c4",
            company_id="netflix",
            name="David",
            crm_outcome=CRMOutcome.NOT_INTERESTED,
            interview_status=InterviewState.NOT_APPLICABLE,
            interested_at=t0,
        )
        now = t0 + timedelta(days=10)
        result = check_contact_follow_up_eligibility(contact, current_time=now)
        assert not result.is_due

    def test_generate_due_reminders_batch_and_deduplication(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        c1 = Contact(
            contact_id="c1",
            company_id="apple",
            name="Contact 1",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=t0,
        )
        c2 = Contact(
            contact_id="c2",
            company_id="microsoft",
            name="Contact 2",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=t0,
        )

        now = t0 + timedelta(days=8)

        # Pre-existing active reminder for c1
        existing = [
            FollowUpReminder.create(
                contact_id="c1",
                due_at=t0 + timedelta(days=7),
            )
        ]

        # Scan batch
        generated = generate_due_reminders(
            contacts=[c1, c2],
            current_time=now,
            threshold_days=7,
            existing_reminders=existing,
        )

        # Only c2 should get a newly generated reminder
        assert len(generated) == 1
        assert generated[0].contact_id == "c2"
        assert generated[0].is_due(now)
