from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel, CRMOutcome, InterviewState, ReminderStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.reminder import FollowUpReminder
from app.ports.infrastructure import FrozenClock
from app.services.contact_service import ContactService
from app.services.context import build_service_context
from app.services.crm_service import CrmService
from app.services.template_service import TemplateService


def make_context(db_session, now):
    return build_service_context(
        db_session,
        event_publisher=MagicMock(),
        clock=FrozenClock(now),
    )


def test_template_crud_and_channel_filtering(db_session):
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    service = TemplateService(db_session, make_context(db_session, now))

    assert service.get_template("missing") is None
    created = service.create_template(
        " custom ",
        " Custom ",
        Channel.EMAIL,
        "Hello",
        subject="Subject",
        attachment_ref=" resume.pdf ",
        phone_number="+1 202 555 0100",
        active=False,
    )
    assert created.id == "custom"
    assert created.attachment_ref == "resume.pdf"
    assert service.get_template("custom").id == created.id
    email_ids = [template.id for template in service.list_templates("email")]
    assert "custom" in email_ids
    assert [template.id for template in service.list_templates("whatsapp", active_only=True)]

    updated = service.update_template("custom", name=" Updated ", body="Updated body", active=True)
    assert updated is not None
    assert updated.name == "Updated"
    assert updated.body == "Updated body"
    assert updated.active is True
    assert service.update_template("missing", name="none") is None

    with pytest.raises(ValueError):
        service.list_templates("sms")


def test_crm_transition_registry_updates_state_and_completes_reminders(db_session):
    now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    context = make_context(db_session, now)
    service = CrmService(db_session, context)
    company = Company.create(name="Acme", company_id="acme")
    context.company_repo.save(company)
    contact = Contact(contact_id="contact-1", company_id=company.id, name="Alice", phone="12025550100")
    context.contact_repo.save(contact)
    reminder = FollowUpReminder.create(contact_id=contact.contact_id, due_at=now, reason="follow up")
    context.reminder_repo.save(reminder)

    result = service.update_status(contact.contact_id, "DO_NOT_CONTACT", now)
    saved = context.contact_repo.get_by_id(contact.contact_id)
    assert result["crm_outcome"] == CRMOutcome.NOT_INTERESTED.value
    assert "do_not_contact" in saved.tags
    saved_reminder = context.reminder_repo.get_by_id(reminder.id)
    assert saved_reminder.status == ReminderStatus.COMPLETED
    assert context.event_publisher.publish_event.called

    with pytest.raises(NotFoundError):
        service.update_status("missing", "INTERESTED", now)
    with pytest.raises(ValidationError):
        service.update_status(contact.contact_id, "INVALID", now)


def test_crm_transition_side_effects_for_interview_offer_closed_and_rejected(db_session):
    now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    context = make_context(db_session, now)
    service = CrmService(db_session, context)
    company = Company.create(name="Acme", company_id="acme-side-effects")
    context.company_repo.save(company)
    outcomes = ["INTERESTED", "INTERVIEW", "OFFER", "CLOSED", "REJECTED"]
    for index, status in enumerate(outcomes):
        contact = Contact(
            contact_id=f"contact-{index}", company_id=company.id, name=status, email=f"{index}@example.com"
        )
        context.contact_repo.save(contact)
        service.update_status(contact.contact_id, status, now)
        saved = context.contact_repo.get_by_id(contact.contact_id)
        assert saved.crm_outcome.value
        if status in {"INTERESTED", "INTERVIEW"}:
            assert saved.interview_status in {InterviewState.PENDING, InterviewState.INTERVIEW}
        if status == "OFFER":
            assert saved.interview_status == InterviewState.INTERVIEW
        if status == "REJECTED":
            assert saved.interview_status == InterviewState.NOT_INTERVIEW


def test_crm_reminder_listing_and_generation_deduplicates(db_session):
    now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    context = make_context(db_session, now)
    service = CrmService(db_session, context)
    company = Company.create(name="Acme", company_id="acme-reminders")
    context.company_repo.save(company)
    contact = Contact(
        contact_id="contact-due",
        company_id=company.id,
        name="Due",
        email="due@example.com",
        crm_outcome=CRMOutcome.INTERESTED,
        interview_status=InterviewState.PENDING,
        interested_at=now - timedelta(days=8),
    )
    context.contact_repo.save(contact)

    created = service.generate_due_reminders(current_time=now)
    assert len(created) == 1
    assert service.generate_due_reminders(current_time=now) == []
    reminders = service.list_reminders(current_time=now)
    assert len(reminders) == 1
    assert reminders[0]["contact_name"] == "Due"
    assert reminders[0]["is_due"] is True


def test_contact_discrepancies_and_archive_tombstones(db_session):
    now = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    context = make_context(db_session, now)
    service = ContactService(db_session, context)
    first_company = Company.create(name="First", company_id="first")
    second_company = Company.create(name="Second", company_id="second")
    context.company_repo.save(first_company)
    context.company_repo.save(second_company)
    first = Contact(
        contact_id="first-contact",
        company_id=first_company.id,
        name="Same",
        phone="12025550100",
        email="same@example.com",
    )
    second = Contact(
        contact_id="second-contact",
        company_id=second_company.id,
        name="Same",
        phone="12025550100",
        email="same@example.com",
    )
    context.contact_repo.save(first)
    context.contact_repo.save(second)

    discrepancies = service.get_discrepancies()
    assert discrepancies["count"] == 1
    assert {item["kind"] for item in discrepancies["groups"]} == {"PHONE"}

    assert service.archive_contact("missing") is False
    assert service.archive_contact(first.contact_id, "TEST_DELETE") is True
    assert context.contact_repo.get_by_id(first.contact_id) is None
    assert context.suppression_repo.is_suppressed(phone="12025550100") is True
    assert context.suppression_repo.is_suppressed(email="same@example.com") is True
    assert context.suppression_repo.is_suppressed(canonical_key=first.canonical_key) is True
    assert context.event_publisher.publish_event.called
