"""Unit tests for Manual Resend Policy."""

from datetime import datetime, timezone
import pytest

from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.sender_account import SenderAccount


class TestResendPolicy:
    def test_prepare_manual_resend_preserves_history(self):
        contact = Contact(
            contact_id="cnt_resend_1",
            company_id="google",
            name="Sundar",
            phone="919876543210",
        )
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="whatsapp_web",
            identity="+919999999999",
            display_name="Sender 1",
        )

        # First historical attempt (SENT)
        attempt_old = OutreachAttempt.prepare(
            contact_id=contact.contact_id,
            sender_account_id=sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Original message",
        )
        attempt_old.mark_sending()
        attempt_old.mark_sent()

        # Trigger manual resend
        t_resend = datetime(2026, 1, 20, 14, 0, 0, tzinfo=timezone.utc)
        resend_attempt = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.WHATSAPP,
            rendered_body="Resend follow-up message",
            historical_attempts=[attempt_old],
            resend_timestamp=t_resend,
        )

        assert resend_attempt.attempt_type == AttemptType.RESEND
        assert resend_attempt.status == OutreachStatus.PREPARED
        assert resend_attempt.message_body_snapshot == "Resend follow-up message"
        assert resend_attempt.prepared_at == t_resend
        assert resend_attempt.id != attempt_old.id
        assert resend_attempt.idempotency_key != attempt_old.idempotency_key

        # Original attempt remains completely intact and untouched
        assert attempt_old.status == OutreachStatus.SENT
        assert attempt_old.message_body_snapshot == "Original message"

    def test_consecutive_resends_generate_distinct_idempotency_keys(self):
        contact = Contact(contact_id="c2", company_id="amazon", name="Andy")
        sender = SenderAccount.create(channel=Channel.EMAIL, provider="smtp", identity="me@ex.com", display_name="Me")

        t1 = datetime(2026, 1, 20, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 22, 10, 0, 0, tzinfo=timezone.utc)

        resend1 = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.EMAIL,
            rendered_body="Resend 1",
            resend_timestamp=t1,
        )
        resend2 = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.EMAIL,
            rendered_body="Resend 2",
            historical_attempts=[resend1],
            resend_timestamp=t2,
        )

        assert resend1.idempotency_key != resend2.idempotency_key
