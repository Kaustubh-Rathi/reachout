"""Unit tests for OutreachAttempt domain entity and state machine."""

from datetime import datetime, timezone

import pytest

from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key


class TestOutreachAttemptModel:
    def test_attempt_preparation_and_snapshots(self):
        t0 = datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_123",
            sender_account_id="snd_wa_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello Alice, exploring SDE roles at Amazon.",
            campaign_id="cmp_001",
            template_id="tmpl_001",
            attachment_ref="resume.pdf",
            prepared_at=t0,
        )
        assert attempt.contact_id == "cnt_123"
        assert attempt.sender_account_id == "snd_wa_1"
        assert attempt.channel == Channel.WHATSAPP
        assert attempt.attempt_type == AttemptType.AUTOMATIC
        assert attempt.status == OutreachStatus.PREPARED
        assert attempt.message_body_snapshot == "Hello Alice, exploring SDE roles at Amazon."
        assert attempt.attachment_snapshot == "resume.pdf"
        assert attempt.prepared_at == t0
        assert attempt.idempotency_key.startswith("idemp_whatsapp_")

    def test_idempotency_key_determinism(self):
        key1 = generate_idempotency_key(
            contact_id="c1",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            campaign_id="cmp_1",
        )
        key2 = generate_idempotency_key(
            contact_id="c1",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            campaign_id="cmp_1",
        )
        assert key1 == key2

        key_diff_channel = generate_idempotency_key(
            contact_id="c1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            campaign_id="cmp_1",
        )
        assert key1 != key_diff_channel

    def test_successful_dispatch_transition(self):
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_1",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Test message",
        )
        assert attempt.status == OutreachStatus.PREPARED

        attempt.mark_queued()
        assert attempt.status == OutreachStatus.QUEUED

        t_send = datetime(2026, 1, 10, 10, 5, 0, tzinfo=timezone.utc)
        attempt.mark_sending(timestamp=t_send)
        assert attempt.status == OutreachStatus.SENDING
        assert attempt.started_at == t_send

        t_done = datetime(2026, 1, 10, 10, 5, 30, tzinfo=timezone.utc)
        attempt.mark_sent(provider_reference="msg_wam_999", timestamp=t_done)
        assert attempt.status == OutreachStatus.SENT
        assert attempt.completed_at == t_done
        assert attempt.provider_reference == "msg_wam_999"
        assert attempt.status.is_successful

    def test_failure_dispatch_transition(self):
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_2",
            sender_account_id="snd_2",
            channel=Channel.EMAIL,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Email body",
        )
        attempt.mark_sending()
        attempt.mark_failed(
            failure_code="ERR_SMTP_550",
            failure_detail="Recipient mailbox unavailable",
        )
        assert attempt.status == OutreachStatus.FAILED
        assert attempt.failure_code == "ERR_SMTP_550"
        assert attempt.failure_detail == "Recipient mailbox unavailable"
        assert attempt.status.is_final

    def test_external_uncertainty_and_recovery_workflow(self):
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_3",
            sender_account_id="snd_wa_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="WhatsApp message",
        )
        attempt.mark_sending()

        # Simulate unexpected crash or browser timeout
        t_crash = datetime(2026, 1, 10, 10, 6, 0, tzinfo=timezone.utc)
        attempt.mark_recovery_required(
            reason="Playwright browser crashed before delivery receipt confirmation",
            timestamp=t_crash,
        )
        assert attempt.status == OutreachStatus.RECOVERY_REQUIRED
        assert attempt.status.requires_attention
        assert not attempt.status.is_final

        # Resolve recovery after operator/audit check
        t_resolve = datetime(2026, 1, 10, 10, 15, 0, tzinfo=timezone.utc)
        attempt.resolve_recovery(
            resolved_status=OutreachStatus.SENT,
            notes="Confirmed message delivered in WhatsApp mobile app audit",
            provider_reference="manual_audit_verified",
            timestamp=t_resolve,
        )
        assert attempt.status == OutreachStatus.SENT
        assert attempt.recovery_notes == "Confirmed message delivered in WhatsApp mobile app audit"
        assert attempt.provider_reference == "manual_audit_verified"
        assert attempt.completed_at == t_resolve

    def test_invalid_recovery_resolution(self):
        attempt = OutreachAttempt.prepare(
            contact_id="cnt_4",
            sender_account_id="snd_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Msg",
        )
        with pytest.raises(ValueError, match="Cannot resolve recovery"):
            attempt.resolve_recovery(
                resolved_status=OutreachStatus.SENT,
                notes="Premature resolution",
            )
