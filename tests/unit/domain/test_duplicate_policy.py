"""Unit tests for Automatic Duplicate Prevention Policy."""

from datetime import datetime, timezone
import pytest

from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility


class TestDuplicatePolicy:
    def test_whatsapp_already_sent_on_contact(self):
        contact = Contact(
            contact_id="c1",
            company_id="google",
            name="Sundar",
            phone="919876543210",
            last_whatsapp_at=datetime.now(timezone.utc),
        )
        res = evaluate_automatic_eligibility(contact, Channel.WHATSAPP)
        assert not res.is_eligible
        assert res.reason == "ALREADY_SENT_WHATSAPP"

    def test_email_already_sent_on_contact(self):
        contact = Contact(
            contact_id="c2",
            company_id="amazon",
            name="Andy",
            email="andy@amazon.com",
            last_email_at=datetime.now(timezone.utc),
        )
        res = evaluate_automatic_eligibility(contact, Channel.EMAIL)
        assert not res.is_eligible
        assert res.reason == "ALREADY_SENT_EMAIL"

    def test_channel_independence_sent_whatsapp_still_allows_email(self):
        """Having sent WhatsApp should not automatically block Email if Email has not been sent."""
        contact = Contact(
            contact_id="c3",
            company_id="meta",
            name="Mark",
            phone="919876543210",
            email="mark@meta.com",
            last_whatsapp_at=datetime.now(timezone.utc),
            last_email_at=None,
        )
        wa_res = evaluate_automatic_eligibility(contact, Channel.WHATSAPP)
        assert not wa_res.is_eligible

        em_res = evaluate_automatic_eligibility(contact, Channel.EMAIL)
        assert em_res.is_eligible
        assert em_res.reason == "ELIGIBLE"

    def test_missing_recipient_details_blocks_eligibility(self):
        no_phone = Contact(contact_id="c4", company_id="c", name="No Phone", email="test@ex.com")
        res_wa = evaluate_automatic_eligibility(no_phone, Channel.WHATSAPP)
        assert not res_wa.is_eligible
        assert res_wa.reason == "MISSING_PHONE_NUMBER"

        no_email = Contact(contact_id="c5", company_id="c", name="No Email", phone="919999999999")
        res_em = evaluate_automatic_eligibility(no_email, Channel.EMAIL)
        assert not res_em.is_eligible
        assert res_em.reason == "MISSING_EMAIL_ADDRESS"

    def test_historical_attempt_sent_blocks_duplicate(self):
        contact = Contact(
            contact_id="c6",
            company_id="apple",
            name="Tim",
            phone="919999999999",
        )
        attempt_sent = OutreachAttempt.prepare(
            contact_id="c6",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )
        attempt_sent.mark_sending()
        attempt_sent.mark_sent()

        res = evaluate_automatic_eligibility(
            contact, Channel.WHATSAPP, historical_attempts=[attempt_sent]
        )
        assert not res.is_eligible
        assert "ALREADY_SENT" in res.reason
        assert res.blocking_attempt_id == attempt_sent.id

    def test_in_flight_attempt_blocks_automatic_resubmission(self):
        contact = Contact(
            contact_id="c7",
            company_id="netflix",
            name="Reed",
            phone="919999999999",
        )
        attempt_queued = OutreachAttempt.prepare(
            contact_id="c7",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )
        attempt_queued.mark_queued()

        res = evaluate_automatic_eligibility(
            contact, Channel.WHATSAPP, historical_attempts=[attempt_queued]
        )
        assert not res.is_eligible
        assert "ATTEMPT_IN_FLIGHT" in res.reason
        assert res.blocking_attempt_id == attempt_queued.id

    def test_failed_historical_attempt_allows_automatic_retry(self):
        contact = Contact(
            contact_id="c8",
            company_id="uber",
            name="Dara",
            phone="919999999999",
        )
        attempt_failed = OutreachAttempt.prepare(
            contact_id="c8",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )
        attempt_failed.mark_sending()
        attempt_failed.mark_failed("ERR_TIMEOUT", "Network timeout")

        res = evaluate_automatic_eligibility(
            contact, Channel.WHATSAPP, historical_attempts=[attempt_failed]
        )
        assert res.is_eligible
        assert res.reason == "ELIGIBLE"
