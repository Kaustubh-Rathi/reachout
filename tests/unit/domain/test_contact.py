"""Unit tests for Contact domain entity and behaviors."""

from datetime import datetime, timedelta, timezone

from app.domain.contact import Contact, extract_first_name
from app.domain.enums import Channel, CRMOutcome, InterviewState


class TestContactModel:
    def test_contact_initialization_and_normalization(self):
        contact = Contact(
            contact_id="cnt_123",
            company_id="google",
            name="Mr. Sundar Pichai (CEO)",
            designation="Chief Executive Officer",
            phone="+91 98765 43210",
            email="SUNDAR@GOOGLE.COM",
        )
        assert contact.contact_id == "cnt_123"
        assert contact.company_id == "google"
        assert contact.phone == "919876543210"
        assert contact.email == "sundar@google.com"
        assert contact.first_name == "Sundar"
        assert contact.canonical_key == "google|919876543210"

    def test_auto_id_generation(self):
        contact = Contact(
            contact_id="",
            company_id="microsoft",
            name="Satya Nadella",
        )
        assert contact.contact_id.startswith("cnt_")
        assert len(contact.contact_id) > 10

    def test_extract_first_name_variations(self):
        assert extract_first_name("Dr. Jane Doe") == "Jane"
        assert extract_first_name("Mrs. Sarah Connor (Talent)") == "Sarah"
        assert extract_first_name("Alex") == "Alex"
        assert extract_first_name("") == "there"
        assert extract_first_name("N/A") == "there"
        assert extract_first_name("prof. albus dumbledore") == "Albus"

    def test_crm_outcome_interested_transition(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c1",
            company_id="amazon",
            name="Jeff",
            crm_outcome=CRMOutcome.NONE,
            interview_status=InterviewState.NOT_APPLICABLE,
        )
        assert contact.interested_at is None
        assert contact.interview_status == InterviewState.NOT_APPLICABLE

        contact.update_crm_outcome(CRMOutcome.INTERESTED, timestamp=t0)
        assert contact.crm_outcome == CRMOutcome.INTERESTED
        assert contact.interested_at == t0
        assert contact.interview_status == InterviewState.PENDING
        assert contact.interview_status_changed_at == t0
        assert contact.last_activity_at == t0

    def test_interview_status_transition(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(days=2)
        contact = Contact(
            contact_id="c1",
            company_id="apple",
            name="Tim",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            interested_at=t0,
        )

        contact.update_interview_status(InterviewState.INTERVIEW, timestamp=t1)
        assert contact.interview_status == InterviewState.INTERVIEW
        assert contact.interview_status_changed_at == t1
        assert contact.last_activity_at == t1

    def test_record_outreach_success_whatsapp(self):
        t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c1",
            company_id="netflix",
            name="Reed",
            phone="919999999999",
            crm_outcome=CRMOutcome.NONE,
        )
        assert not contact.has_successful_outreach(Channel.WHATSAPP)
        assert not contact.has_successful_outreach(Channel.EMAIL)

        contact.record_outreach_success(Channel.WHATSAPP, timestamp=t0)
        assert contact.last_whatsapp_at == t0
        assert contact.last_activity_at == t0
        assert contact.crm_outcome == CRMOutcome.PENDING_REPLY
        assert contact.has_successful_outreach(Channel.WHATSAPP)
        assert not contact.has_successful_outreach(Channel.EMAIL)

    def test_record_outreach_success_email(self):
        t0 = datetime(2026, 1, 5, 11, 0, 0, tzinfo=timezone.utc)
        contact = Contact(
            contact_id="c1",
            company_id="meta",
            name="Mark",
            email="mark@meta.com",
            crm_outcome=CRMOutcome.NONE,
        )
        contact.record_outreach_success(Channel.EMAIL, timestamp=t0)
        assert contact.last_email_at == t0
        assert contact.last_activity_at == t0
        assert contact.crm_outcome == CRMOutcome.PENDING_REPLY
        assert contact.has_successful_outreach(Channel.EMAIL)

    def test_contact_does_not_contain_message_history(self):
        """Verify SRP: Contact holds profile and milestones, not full attempt logs."""
        contact = Contact(contact_id="c1", company_id="uber", name="Dara")
        assert not hasattr(contact, "messages")
        assert not hasattr(contact, "attempts")
        assert not hasattr(contact, "history")
