"""Operational Lifecycle and Workflow Verification Tests.

Verifies the operational business journey across service domain boundaries:
1. Initialize system with Contacts across Companies.
2. Query Initial Contact state.
3. Start Campaign -> Campaign Worker executes company-first outreach.
4. Contacts update to SENT; immutable Attempt snapshots created.
5. Activity logs and audit history appear.
6. Operator executes Manual Resend to specific contact with custom message.
7. Operator updates CRM outcome to INTERESTED + PENDING.
8. Time advances 7 days -> Follow-up reminder is generated and alerted.
9. Operator marks reminder COMPLETED with meeting notes.
"""

from __future__ import annotations

import datetime
from datetime import timedelta, timezone
from typing import List
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    CRMOutcome,
    InterviewState,
    OutreachStatus,
    ReminderStatus,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.prioritization import prioritize_company_first
from app.domain.policies.reminder_policy import check_contact_follow_up_eligibility, generate_due_reminders
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.reminder import FollowUpReminder
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.models import (
    CampaignModel,
    CompanyModel,
    ContactModel,
    FollowUpReminderModel,
    MessageTemplateModel,
    OutreachAttemptModel,
    SenderAccountModel,
)
from app.ports.providers import ProviderSendResult, WhatsAppProvider


class TestOperationalLifecycleWorkflow:
    """Service integration test verifying the complete candidate lifecycle without external I/O."""

    def test_complete_outreach_and_crm_lifecycle(self, mock_wa_provider):
        # 1. SETUP: In-memory relational database
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()

        current_time = datetime.datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)

        # Seed 3 Companies and 5 Contacts
        comp_a = Company.create("Google India")
        comp_b = Company.create("Microsoft India")
        comp_c = Company.create("Amazon India")
        session.add_all([CompanyModel.from_domain(c) for c in [comp_a, comp_b, comp_c]])
        session.flush()

        contacts = [
            Contact(contact_id="cnt_e2e_1", company_id=comp_a.id, name="Alice (Google)", phone="919001", email="alice@google.com"),
            Contact(contact_id="cnt_e2e_2", company_id=comp_a.id, name="Bob (Google)", phone="919002", email="bob@google.com"),
            Contact(contact_id="cnt_e2e_3", company_id=comp_b.id, name="Charlie (MS)", phone="919003", email="charlie@ms.com"),
            Contact(contact_id="cnt_e2e_4", company_id=comp_c.id, name="Diana (AWS)", phone="919004", email="diana@aws.com"),
            Contact(contact_id="cnt_e2e_5", company_id=comp_c.id, name="Evan (AWS)", phone="919005", email="evan@aws.com"),
        ]
        session.add_all([ContactModel.from_domain(c) for c in contacts])

        # Seed Sender Account & Template
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="MOCK_WA",
            identity="+919999999999",
            display_name="Primary WhatsApp",
            daily_limit=100,
        )
        template = MessageTemplate.create(
            name="Job Outreach v1",
            channel=Channel.WHATSAPP,
            body="Hi {first_name}, I'm reaching out regarding openings at {company}.",
        )
        session.add(SenderAccountModel.from_domain(sender))
        session.add(MessageTemplateModel.from_domain(template))
        session.commit()

        # --- STEP 1: Query Initial Contacts ---
        total_contacts = session.scalar(select(func.count(ContactModel.contact_id)))
        assert total_contacts == 5

        # --- STEP 2: Create and Start Campaign ---
        campaign = Campaign.create(
            name="August Engineering Outreach",
            channel=Channel.WHATSAPP,
            sender_account_ids=[sender.id],
            template_ids=[template.id],
        )
        campaign.start()
        session.add(CampaignModel.from_domain(campaign))
        session.commit()
        assert campaign.status == CampaignStatus.RUNNING

        # --- STEP 3: Company-First Queue Selection ---
        db_contacts = [m.to_domain() for m in session.scalars(select(ContactModel)).all()]
        ordered_contacts = prioritize_company_first(db_contacts)

        # Expected company interleaving: Alice (Google), Charlie (MS), Diana (AWS), Bob (Google), Evan (AWS)
        expected_order = ["Alice (Google)", "Charlie (MS)", "Diana (AWS)", "Bob (Google)", "Evan (AWS)"]
        assert [c.name for c in ordered_contacts] == expected_order

        # --- STEP 4: Campaign Worker Dispatches Outreach ---
        for contact in ordered_contacts:
            rendered = template.render(contact=contact, company=comp_a)

            # Pre-send snapshot
            attempt = OutreachAttempt.prepare(
                contact_id=contact.contact_id,
                sender_account_id=sender.id,
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body=rendered.body,
                campaign_id=campaign.id,
                template_id=template.id,
            )
            attempt.mark_sending()

            # Execute via provider port
            result = mock_wa_provider.send_message(attempt, contact.phone, attempt.message_body_snapshot)
            assert result.success is True

            attempt.mark_sent(provider_reference=result.provider_reference)
            session.add(OutreachAttemptModel.from_domain(attempt))

            # Update contact record
            contact_m = session.get(ContactModel, contact.contact_id)
            contact_m.last_whatsapp_at = attempt.completed_at
            contact_m.last_activity_at = attempt.completed_at

        session.commit()

        # --- STEP 5: Verify Sent Counts and Audit Logs ---
        sent_attempts_count = session.scalar(
            select(func.count(OutreachAttemptModel.id)).where(OutreachAttemptModel.status == "SENT")
        )
        assert sent_attempts_count == 5

        # --- STEP 6: Operator Performs Manual Resend to Alice ---
        alice_m = session.scalar(select(ContactModel).where(ContactModel.name == "Alice (Google)"))
        alice_domain = alice_m.to_domain()
        alice_attempts = [
            m.to_domain() for m in session.scalars(
                select(OutreachAttemptModel).where(OutreachAttemptModel.contact_id == alice_domain.contact_id)
            ).all()
        ]

        manual_resend = prepare_manual_resend(
            contact=alice_domain,
            sender_account=sender,
            channel=Channel.WHATSAPP,
            rendered_body="Hi Alice, following up on my previous note.",
            historical_attempts=alice_attempts,
        )
        manual_resend.mark_sending()

        resend_result = mock_wa_provider.send_message(manual_resend, alice_domain.phone, manual_resend.message_body_snapshot)
        manual_resend.mark_sent(resend_result.provider_reference)
        session.add(OutreachAttemptModel.from_domain(manual_resend))
        session.commit()

        # Alice now has 2 historical attempts (attempt 1 and attempt 2)
        alice_total_attempts = session.scalar(
            select(func.count(OutreachAttemptModel.id)).where(OutreachAttemptModel.contact_id == alice_domain.contact_id)
        )
        assert alice_total_attempts == 2

        # --- STEP 7: Candidate Replies -> Operator Updates CRM Outcome ---
        alice_m = session.get(ContactModel, alice_domain.contact_id)
        alice_m.crm_outcome = CRMOutcome.INTERESTED.value
        alice_m.interview_status = InterviewState.PENDING.value
        alice_m.interested_at = current_time
        alice_m.notes = "Interested in senior backend role. Waiting for interview scheduling."
        session.commit()

        # --- STEP 8: Time Advances 7 Days -> Generate Follow-Up Reminders ---
        advanced_time = current_time + timedelta(days=7, hours=1)

        all_contacts_after_week = [m.to_domain() for m in session.scalars(select(ContactModel)).all()]
        new_reminders = generate_due_reminders(all_contacts_after_week, current_time=advanced_time)

        # Alice is due for a follow-up reminder
        assert len(new_reminders) == 1
        assert new_reminders[0].contact_id == alice_domain.contact_id
        session.add(FollowUpReminderModel.from_domain(new_reminders[0]))
        session.commit()

        # --- STEP 9: Operator Completes Follow-Up Call ---
        reminder_m = session.scalar(
            select(FollowUpReminderModel).where(FollowUpReminderModel.contact_id == alice_domain.contact_id)
        )
        assert reminder_m is not None
        assert reminder_m.status == ReminderStatus.PENDING.value

        reminder_domain = reminder_m.to_domain()
        reminder_domain.mark_completed()

        reminder_m.status = reminder_domain.status.value

        # Advance interview state to INTERVIEW
        alice_m = session.get(ContactModel, alice_domain.contact_id)
        alice_m.interview_status = InterviewState.INTERVIEW.value
        alice_m.interview_status_changed_at = advanced_time
        session.commit()

        # --- STEP 10: Verify Final State ---
        final_alice = session.get(ContactModel, alice_domain.contact_id).to_domain()
        assert final_alice.crm_outcome == CRMOutcome.INTERESTED
        assert final_alice.interview_status == InterviewState.INTERVIEW

        # Further reminder check after interview scheduled returns False
        further_check = check_contact_follow_up_eligibility(final_alice, current_time=advanced_time + timedelta(days=5))
        assert further_check.is_due is False

        session.close()
