"""Unit tests for Domain State Transitions and Enum Properties."""

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


class TestStateTransitionsAndEnums:
    def test_campaign_status_properties(self):
        assert CampaignStatus.RUNNING.is_active
        assert CampaignStatus.STARTING.is_active
        assert not CampaignStatus.IDLE.is_active
        assert not CampaignStatus.PAUSED.is_active

        assert CampaignStatus.COMPLETED.is_terminal
        assert CampaignStatus.STOPPED.is_terminal
        assert CampaignStatus.FAILED.is_terminal
        assert not CampaignStatus.RUNNING.is_terminal

    def test_outreach_status_properties(self):
        assert OutreachStatus.SENT.is_successful
        assert not OutreachStatus.FAILED.is_successful
        assert not OutreachStatus.UNKNOWN.is_successful

        assert OutreachStatus.QUEUED.is_in_flight
        assert OutreachStatus.SENDING.is_in_flight
        assert not OutreachStatus.PREPARED.is_in_flight

        assert OutreachStatus.UNKNOWN.requires_attention
        assert OutreachStatus.RECOVERY_REQUIRED.requires_attention
        assert not OutreachStatus.SENT.requires_attention

        assert OutreachStatus.SENT.is_final
        assert OutreachStatus.FAILED.is_final
        assert not OutreachStatus.RECOVERY_REQUIRED.is_final

    def test_crm_outcome_properties(self):
        assert CRMOutcome.INTERESTED.is_positive
        assert CRMOutcome.REFERRAL_GIVEN.is_positive
        assert not CRMOutcome.NOT_INTERESTED.is_positive
        assert not CRMOutcome.NONE.is_positive

        assert CRMOutcome.INTERESTED.is_replied
        assert CRMOutcome.NOT_INTERESTED.is_replied
        assert CRMOutcome.REPLIED_NO_OPENINGS.is_replied
        assert not CRMOutcome.NONE.is_replied
        assert not CRMOutcome.PENDING_REPLY.is_replied
        assert not CRMOutcome.GHOSTED.is_replied

    def test_sender_status_properties(self):
        assert SenderStatus.ACTIVE.is_usable
        assert not SenderStatus.INACTIVE.is_usable
        assert not SenderStatus.RATE_LIMITED.is_usable
        assert not SenderStatus.DISCONNECTED.is_usable
        assert not SenderStatus.SUSPENDED.is_usable

    def test_string_representations(self):
        assert str(Channel.WHATSAPP) == "WHATSAPP"
        assert str(Channel.EMAIL) == "EMAIL"
        assert str(AttemptType.RESEND) == "RESEND"
        assert str(InterviewState.PENDING) == "PENDING"
        assert str(ReminderStatus.PENDING) == "PENDING"
