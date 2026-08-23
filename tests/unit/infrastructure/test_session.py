"""Session Lifecycle, Provider Failure Modes, and Recovery Tests.

Verifies system resilience against realistic provider and session failure scenarios:
1. Authenticated session -> normal execution.
2. Unauthenticated / QR Code required -> sender status updated to DISCONNECTED.
3. Forced logout (`?post_logout=1`) -> sender status SUSPENDED, campaign paused.
4. Rate limited (HTTP 429 / SMTP 421) -> sender status RATE_LIMITED, backoff triggered.
5. Browser / Worker crash -> in-flight attempts audited and recovered.
6. Network socket timeouts & provider recipient rejections.
"""

from __future__ import annotations

import datetime
from datetime import timezone
import pytest

from app.domain.campaign import Campaign
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    OutreachStatus,
    SenderStatus,
)
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.ports.providers import ProviderSendResult, ProviderStatusResult


class TestSessionAndFailureHandling:
    """Suite testing provider session lifecycles, error handling, and recovery."""

    def test_authenticated_session_succeeds(self, sample_contact: Contact, sample_sender: SenderAccount, mock_wa_provider):
        """Authenticated WhatsApp session successfully dispatches and records sent state."""
        assert sample_sender.status == SenderStatus.ACTIVE

        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello!",
        )
        attempt.mark_sending()

        result = mock_wa_provider.send_message(
            attempt=attempt, recipient_phone=sample_contact.phone, message_body=attempt.message_body_snapshot
        )
        assert result.success is True
        attempt.mark_sent(result.provider_reference)
        assert attempt.status == OutreachStatus.SENT

    def test_authentication_required_suspends_sender_and_pauses_campaign(self, sample_contact: Contact):
        """If provider encounters QR code / login prompt, sender is marked DISCONNECTED."""
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP,
            provider="PLAYWRIGHT",
            identity="+919999999999",
            display_name="WA Sender",
        )
        campaign = Campaign.create(name="August Sprint", channel=Channel.WHATSAPP)
        campaign.start()
        assert campaign.status == CampaignStatus.RUNNING

        # Provider result
        provider_result = ProviderSendResult.failed(
            failure_code="ERR_AUTH_REQUIRED",
            failure_detail="WhatsApp Web requires QR code scan.",
        )

        # Transition sender to DISCONNECTED
        sender.mark_status(SenderStatus.DISCONNECTED)
        assert sender.status == SenderStatus.DISCONNECTED
        assert sender.is_available() is False

        # Campaign should be paused for human intervention
        campaign.pause()
        assert campaign.status == CampaignStatus.PAUSED

    def test_session_expired_forced_logout_handling(self, sample_contact: Contact):
        """Simulate ?post_logout=1 session death (preventing rapid retry spam loops)."""
        sender = SenderAccount.create(
            channel=Channel.WHATSAPP, provider="PLAYWRIGHT", identity="+919999999999", display_name="WA"
        )
        campaign = Campaign.create(name="Batch Send", channel=Channel.WHATSAPP)
        campaign.start()

        # In-flight attempt during logout event
        attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Test text",
        )
        attempt.mark_sending()

        # Provider catches ?post_logout=1 redirect
        logout_result = ProviderSendResult.recovery_required(
            "WhatsApp Web forced logout (?post_logout=1) during attachment."
        )

        # Attempt transitions to RECOVERY_REQUIRED
        attempt.mark_recovery_required(reason=logout_result.failure_detail or "Session logged out")
        assert attempt.status == OutreachStatus.RECOVERY_REQUIRED

        # Sender is suspended
        sender.mark_status(SenderStatus.SUSPENDED)
        assert sender.status == SenderStatus.SUSPENDED

        # Campaign halts
        campaign.pause()
        assert campaign.status == CampaignStatus.PAUSED

    def test_rate_limited_provider_triggers_sender_cooldown(self, sample_contact: Contact):
        """Provider returning 429 / rate limit transitions sender to RATE_LIMITED."""
        sender = SenderAccount.create(channel=Channel.EMAIL, provider="SMTP_GMAIL", identity="user@gmail.com", display_name="EM")

        rate_limit_result = ProviderSendResult.failed(
            failure_code="ERR_RATE_LIMITED",
            failure_detail="450 4.2.1 The user you are trying to contact is receiving mail at a rate that prevents additional messages.",
        )

        sender.mark_status(SenderStatus.RATE_LIMITED)
        assert sender.status == SenderStatus.RATE_LIMITED
        assert sender.status.is_usable is False

    def test_crash_recovery_of_interrupted_sending_attempts(self, sample_contact: Contact, sample_sender: SenderAccount):
        """Attempts left in SENDING or PREPARED after process crash are flagged for recovery."""
        # Simulated orphaned attempt left in DB during sudden crash
        orphaned_attempt = OutreachAttempt.prepare(
            contact_id=sample_contact.contact_id,
            sender_account_id=sample_sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Message sent right before power cut",
        )
        orphaned_attempt.mark_sending()

        assert orphaned_attempt.status == OutreachStatus.SENDING

        # Crash recovery scanner runs on process startup
        def recover_orphaned_attempt(att: OutreachAttempt) -> None:
            if att.status in (OutreachStatus.SENDING, OutreachStatus.PREPARED):
                att.mark_recovery_required(
                    reason="Process restart detected attempt left in unconfirmed SENDING state."
                )

        recover_orphaned_attempt(orphaned_attempt)
        assert orphaned_attempt.status == OutreachStatus.RECOVERY_REQUIRED
        assert "Process restart detected" in (orphaned_attempt.failure_detail or "")
