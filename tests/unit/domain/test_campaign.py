"""Unit tests for Campaign domain entity and lifecycle state machine."""

from datetime import datetime, timezone

import pytest

from app.domain.campaign import Campaign
from app.domain.enums import CampaignStatus, Channel


class TestCampaignModel:
    def test_campaign_creation(self):
        camp = Campaign.create(
            name="Q1 SDE Outreach",
            channel=Channel.WHATSAPP,
            template_ids=["tmpl_1", "tmpl_2"],
            sender_account_ids=["snd_1"],
        )
        assert camp.name == "Q1 SDE Outreach"
        assert camp.channel == Channel.WHATSAPP
        assert camp.status == CampaignStatus.IDLE
        assert camp.template_ids == ["tmpl_1", "tmpl_2"]
        assert camp.sender_account_ids == ["snd_1"]
        assert camp.started_at is None
        assert camp.ended_at is None

    def test_lifecycle_happy_path(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        camp = Campaign.create(name="Batch A", channel=Channel.EMAIL)

        # Start
        camp.start(timestamp=t0)
        assert camp.status == CampaignStatus.RUNNING
        assert camp.started_at == t0
        assert camp.status.is_active

        # Pause
        camp.pause()
        assert camp.status == CampaignStatus.PAUSED
        assert not camp.status.is_active

        # Resume
        camp.resume()
        assert camp.status == CampaignStatus.RUNNING

        # Complete
        t1 = datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
        camp.complete(timestamp=t1)
        assert camp.status == CampaignStatus.COMPLETED
        assert camp.ended_at == t1
        assert camp.status.is_terminal

    def test_campaign_stop(self):
        camp = Campaign.create(name="Batch B", channel=Channel.WHATSAPP)
        camp.start()
        camp.stop()
        assert camp.status == CampaignStatus.STOPPED
        assert camp.status.is_terminal

    def test_campaign_failure(self):
        camp = Campaign.create(name="Batch C", channel=Channel.EMAIL)
        camp.start()
        camp.fail(reason="SMTP provider authentication rejected")
        assert camp.status == CampaignStatus.FAILED
        assert camp.metadata.get("failure_reason") == "SMTP provider authentication rejected"

    def test_invalid_transitions_raise_error(self):
        camp = Campaign.create(name="Batch D", channel=Channel.WHATSAPP)

        # Cannot pause while IDLE
        with pytest.raises(ValueError, match="Cannot pause"):
            camp.pause()

        # Cannot complete while IDLE
        with pytest.raises(ValueError, match="Cannot complete"):
            camp.complete()

        # Start then complete
        camp.start()
        camp.complete()

        # Cannot restart completed campaign
        with pytest.raises(ValueError, match="Cannot start"):
            camp.start()

        # Cannot stop already completed campaign
        with pytest.raises(ValueError, match="already in terminal"):
            camp.stop()
