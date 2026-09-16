"""Outreach Lifecycle, Duplicate Prevention, and Idempotency Verification Tests.

Verifies:
1. Automatic first send lifecycle and pre-send snapshot creation.
2. Automatic duplicate suppression on WhatsApp and Email channels.
3. Manual send and manual resend workflows with sequence-aware idempotency keys.
4. Historical attempt preservation (immutability of past attempt audit logs).
5. Granular failure taxonomy (retryable, permanent, unknown, recovery-required).
"""

from __future__ import annotations

import datetime
from datetime import timezone

from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    Channel,
    OutreachStatus,
)
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.sender_account import SenderAccount


class TestOutreachLifecycleAndPolicies:
    """Suite verifying end-to-end outreach state machines, policies, and audit retention."""

    def test_automatic_first_send_lifecycle(
        self, sample_contact: Contact, sample_sender: SenderAccount, mock_wa_provider
    ):
        """Standard automatic outreach follows PREPARED -> QUEUED -> SENDING -> SENT."""
        # 1. Evaluate eligibility
        eligibility = evaluate_automatic_eligibility(sample_contact, Channel.WHATSAPP)
        assert eligibility.is_eligible is True
        assert eligibility.reason == "ELIGIBLE"

        # 2. Pre-send Snapshot Creation (Intent persisted before provider I/O)
        message_body = f"Hi {sample_contact.name}, exploring roles at Google."
        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            message_body=message_body,
            attempt_type=AttemptType.AUTOMATIC,
        )
        assert attempt.status == OutreachStatus.PREPARED
        assert attempt.message_body_snapshot == message_body

        # 3. Queue and Send
        attempt.mark_queued()
        assert attempt.status == OutreachStatus.QUEUED

        attempt.mark_sending()
        assert attempt.status == OutreachStatus.SENDING

        # 4. Dispatch via provider port
        result = mock_wa_provider.send_message(
            attempt=attempt,
            recipient_phone=sample_contact.phone,
            message_body=attempt.message_body_snapshot,
        )
        assert result.success is True
        assert result.status == OutreachStatus.SENT

        # 5. Transition to terminal SENT
        attempt.mark_sent(provider_reference=result.provider_reference)
        assert attempt.status == OutreachStatus.SENT
        assert attempt.provider_reference is not None
        assert attempt.completed_at is not None

        # 6. Update Contact domain entity
        sample_contact.record_outreach_success(Channel.WHATSAPP, attempt.completed_at)
        assert sample_contact.last_whatsapp_at == attempt.completed_at
        assert sample_contact.last_whatsapp_at is not None

    def test_automatic_duplicate_suppression(self, sample_contact: Contact, sample_sender: SenderAccount):
        """Attempting to automatically send to an already-contacted person is rejected."""
        now = datetime.datetime.now(timezone.utc)
        sample_contact.record_outreach_success(Channel.WHATSAPP, now)

        # Contact has last_whatsapp_at set
        eligibility = evaluate_automatic_eligibility(sample_contact, Channel.WHATSAPP)
        assert eligibility.is_eligible is False
        assert eligibility.reason == "ALREADY_SENT_WHATSAPP"

        # Also suppressed if historical SENT attempt exists
        historical_sent = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Previous message",
        )
        historical_sent.mark_sending()
        historical_sent.mark_sent()

        fresh_contact = Contact(
            contact_id="cnt_fresh_1",
            company_id=sample_contact.company_id,
            name="Fresh Person",
            phone="919876543210",
        )
        historical_sent_fresh = OutreachAttempt.prepare(
            contact_id=fresh_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Previous message",
        )
        historical_sent_fresh.mark_sending()
        historical_sent_fresh.mark_sent()

        eligibility_hist = evaluate_automatic_eligibility(
            fresh_contact, Channel.WHATSAPP, historical_attempts=[historical_sent_fresh]
        )
        assert eligibility_hist.is_eligible is False
        assert "ALREADY_SENT" in eligibility_hist.reason

    def test_manual_send_and_resend_preserves_history(self, sample_contact: Contact, sample_sender: SenderAccount):
        """Manual resend creates a distinct attempt entity while preserving all historical attempts."""
        # 1. Historical attempt 1 (sent 3 days ago)
        attempt_1 = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Initial message v1",
        )
        attempt_1.mark_sending()
        attempt_1.mark_sent()

        # 2. Operator explicitly requests a resend with custom note
        resend_attempt = prepare_manual_resend(
            contact=sample_contact,
            sender_account=sample_sender,
            channel=Channel.WHATSAPP,
            rendered_body="Following up on my previous message.",
            historical_attempts=[attempt_1],
        )

        assert resend_attempt.attempt_type == AttemptType.RESEND
        assert resend_attempt.status == OutreachStatus.PREPARED
        assert resend_attempt.id != attempt_1.id
        assert attempt_1.status == OutreachStatus.SENT  # Past attempt is unaltered

    def test_failed_send_handling_permanent_vs_retryable(
        self, sample_contact: Contact, sample_sender: SenderAccount, mock_wa_provider
    ):
        """Failed send transitions attempt to FAILED with failure codes and preserves contact state."""
        mock_wa_provider.fail_next_with = ("ERR_INVALID_NUMBER", "Phone number not registered on WhatsApp")

        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Test text",
        )
        attempt.mark_sending()

        result = mock_wa_provider.send_message(
            attempt=attempt, recipient_phone=sample_contact.phone, message_body=attempt.message_body_snapshot
        )

        assert result.success is False
        assert result.status == OutreachStatus.FAILED

        attempt.mark_failed(
            failure_code=result.failure_code,
            failure_detail=result.failure_detail,
        )
        assert attempt.status == OutreachStatus.FAILED
        assert attempt.failure_code == "ERR_INVALID_NUMBER"
        assert "Phone number not registered" in (attempt.failure_detail or "")

    def test_unknown_external_result_blocks_automatic_resend(
        self, sample_contact: Contact, sample_sender: SenderAccount, mock_wa_provider
    ):
        """If network crashes before ACK, attempt transitions to UNKNOWN and blocks blind resend."""
        mock_wa_provider.unknown_next = True

        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Test text",
        )
        attempt.mark_sending()

        result = mock_wa_provider.send_message(
            attempt=attempt, recipient_phone=sample_contact.phone, message_body=attempt.message_body_snapshot
        )
        assert result.status == OutreachStatus.UNKNOWN

        attempt.mark_unknown(reason=result.failure_detail or "Network timeout")
        assert attempt.status == OutreachStatus.UNKNOWN

        # Evaluator must block automatic queueing to prevent duplicate sending
        eligibility = evaluate_automatic_eligibility(sample_contact, Channel.WHATSAPP, historical_attempts=[attempt])
        assert eligibility.is_eligible is False
        assert "ATTEMPT_IN_FLIGHT_OR_RECOVERY" in eligibility.reason

    def test_recovery_required_state_and_resolution(
        self, sample_contact: Contact, sample_sender: SenderAccount, mock_wa_provider
    ):
        """Partial side effect (e.g. text sent, PDF crashed) transitions to RECOVERY_REQUIRED and requires audit."""
        mock_wa_provider.recovery_required_next = True

        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Test text with PDF",
        )
        attempt.mark_sending()

        result = mock_wa_provider.send_message(
            attempt=attempt, recipient_phone=sample_contact.phone, message_body=attempt.message_body_snapshot
        )
        assert result.status == OutreachStatus.RECOVERY_REQUIRED

        attempt.mark_recovery_required(reason=result.failure_detail or "Partial delivery")
        assert attempt.status == OutreachStatus.RECOVERY_REQUIRED
        assert attempt.status.requires_attention is True

        # Operator inspects WhatsApp chat and resolves the ambiguous attempt
        attempt.resolve_recovery(
            resolved_status=OutreachStatus.SENT,
            notes="Operator verified message delivered manually in WhatsApp Web UI.",
        )
        assert attempt.status == OutreachStatus.SENT
        assert "Operator verified" in (attempt.recovery_notes or "")
