"""CRM Response Workflows and 7-Day Follow-Up Reminder Tests.

Verifies domain rules:
1. Interested + Interview Pending + 7 days elapsed -> Follow-Up Reminder generated.
2. Interested + Interview Scheduled -> NO reminder.
3. Interested + Not Interview (declined/opted out) -> NO reminder.
4. Not Interested / No Openings / Ghosted -> NO reminder.
5. Batch reminder generation deduplication and dismissal lifecycle.
"""

from __future__ import annotations

import datetime
from datetime import timedelta, timezone

import pytest

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import CRMOutcome, InterviewState, ReminderStatus
from app.domain.policies.reminder_policy import (
    check_contact_follow_up_eligibility,
    generate_due_reminders,
)
from app.domain.reminder import FollowUpReminder


class TestCRMWorkflowAndReminders:
    """Suite verifying CRM state progression and follow-up nurturing rules."""

    def test_interested_and_pending_after_7_days_generates_reminder(self, sample_company: Company):
        """Interested candidate with Pending interview for >= 7 days triggers reminder."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        eight_days_ago = now - timedelta(days=8)

        contact = Contact(
            contact_id="cnt_alice",
            company_id=sample_company.id,
            name="Alice Walker",
            phone="919876543210",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=eight_days_ago,
        )

        eligibility = check_contact_follow_up_eligibility(contact, current_time=now)
        assert eligibility.is_due is True
        assert eligibility.elapsed_days >= 7.0
        assert ">= 7 days threshold" in eligibility.reason

    def test_interested_and_pending_under_7_days_does_not_trigger_reminder(self, sample_company: Company):
        """Interested candidate at 3 days (< 7 days) does NOT trigger reminder."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        three_days_ago = now - timedelta(days=3)

        contact = Contact(
            contact_id="cnt_bob",
            company_id=sample_company.id,
            name="Bob Martin",
            phone="919876543211",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=three_days_ago,
        )

        eligibility = check_contact_follow_up_eligibility(contact, current_time=now)
        assert eligibility.is_due is False
        assert eligibility.elapsed_days < 7.0
        assert "not yet due" in eligibility.reason

    def test_interested_with_interview_scheduled_does_not_trigger_reminder(self, sample_company: Company):
        """Interested candidate who has an interview scheduled (INTERVIEW) triggers NO reminder."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        ten_days_ago = now - timedelta(days=10)

        contact = Contact(
            contact_id="cnt_charlie",
            company_id=sample_company.id,
            name="Charlie Brown",
            phone="919876543212",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.INTERVIEW,
            interested_at=ten_days_ago,
        )

        eligibility = check_contact_follow_up_eligibility(contact, current_time=now)
        assert eligibility.is_due is False
        assert "INTERVIEW, not PENDING" in eligibility.reason

    def test_interested_with_not_interview_does_not_trigger_reminder(self, sample_company: Company):
        """Interested candidate who decided not to interview (NOT_INTERVIEW) triggers NO reminder."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        ten_days_ago = now - timedelta(days=10)

        contact = Contact(
            contact_id="cnt_diana",
            company_id=sample_company.id,
            name="Diana Prince",
            phone="919876543213",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.NOT_INTERVIEW,
            interested_at=ten_days_ago,
        )

        eligibility = check_contact_follow_up_eligibility(contact, current_time=now)
        assert eligibility.is_due is False
        assert "NOT_INTERVIEW, not PENDING" in eligibility.reason

    @pytest.mark.parametrize(
        "outcome",
        [
            CRMOutcome.NOT_INTERESTED,
            CRMOutcome.REPLIED_NO_OPENINGS,
            CRMOutcome.REFERRAL_GIVEN,
            CRMOutcome.NOT_HIRING_FRESHERS,
            CRMOutcome.GHOSTED,
            CRMOutcome.NONE,
        ],
    )
    def test_non_interested_outcomes_do_not_trigger_reminder(self, sample_company: Company, outcome: CRMOutcome):
        """Non-INTERESTED outcomes never trigger follow-up reminders."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        fifteen_days_ago = now - timedelta(days=15)

        contact = Contact(
            contact_id="cnt_evan",
            company_id=sample_company.id,
            name="Evan Wright",
            phone="919876543214",
            crm_outcome=outcome,
            interview_status=InterviewState.PENDING,
            interested_at=fifteen_days_ago,
        )

        eligibility = check_contact_follow_up_eligibility(contact, current_time=now)
        assert eligibility.is_due is False
        assert "CRM outcome is not INTERESTED" in eligibility.reason

    def test_batch_reminder_generation_and_deduplication(self, sample_company: Company):
        """Batch generator creates reminders for due contacts and avoids duplicate pending reminders."""
        now = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        nine_days_ago = now - timedelta(days=9)

        c1 = Contact(
            contact_id="cnt_lead1",
            company_id=sample_company.id,
            name="Lead 1",
            phone="919001",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=nine_days_ago,
        )
        c2 = Contact(
            contact_id="cnt_lead2",
            company_id=sample_company.id,
            name="Lead 2",
            phone="919002",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=nine_days_ago,
        )

        # Existing pending reminder for c1
        existing_r1 = FollowUpReminder.create(
            contact_id=c1.contact_id,
            due_at=now,
            reason="Prior reminder",
        )

        # Run batch generation
        reminders = generate_due_reminders(
            contacts=[c1, c2],
            current_time=now,
            existing_reminders=[existing_r1],
        )

        # Only c2 should get a new reminder
        assert len(reminders) == 1
        assert reminders[0].contact_id == c2.contact_id
        assert reminders[0].status == ReminderStatus.PENDING

        # Mark reminder complete
        reminders[0].mark_completed()
        assert reminders[0].status == ReminderStatus.COMPLETED
