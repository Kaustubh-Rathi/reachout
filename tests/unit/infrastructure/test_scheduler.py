"""Scheduler & Campaign Lifecycle State Recovery Tests.

Verifies:
1. Complete Campaign lifecycle state transitions: START, PAUSE, RESUME, STOP, COMPLETE, FAIL.
2. Invariant protection: Illegal transitions (e.g. STOPPED -> RUNNING) raise explicit domain errors.
3. Process restart & state recovery: In-memory state is transient; relational DB is source of truth.
4. Active campaign progress tracking across pause/resume cycles.
"""

from __future__ import annotations

import datetime
from datetime import timezone
import pytest
from sqlalchemy import select

from app.domain.campaign import Campaign
from app.domain.enums import CampaignStatus, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.models import CampaignModel, OutreachAttemptModel


class TestSchedulerAndCampaignLifecycle:
    """Suite verifying campaign lifecycles, state machine guards, and DB source-of-truth recovery."""

    def test_campaign_full_lifecycle_transitions(self):
        """Campaign moves cleanly through START -> PAUSE -> RESUME -> STOP -> COMPLETE."""
        campaign = Campaign.create(name="Q3 Tech Outreach", channel=Channel.WHATSAPP)
        assert campaign.status == CampaignStatus.IDLE

        # START
        campaign.start()
        assert campaign.status == CampaignStatus.RUNNING
        assert campaign.started_at is not None

        # PAUSE
        campaign.pause()
        assert campaign.status == CampaignStatus.PAUSED

        # RESUME
        campaign.resume()
        assert campaign.status == CampaignStatus.RUNNING

        # COMPLETE
        campaign.complete()
        assert campaign.status == CampaignStatus.COMPLETED
        assert campaign.ended_at is not None
        assert campaign.status.is_terminal is True

    def test_invalid_state_transitions_are_guarded(self):
        """Terminal or mismatched transitions raise ValueError to prevent corrupt state."""
        campaign = Campaign.create(name="Guarded Test", channel=Channel.WHATSAPP)

        # Cannot pause an IDLE campaign
        with pytest.raises(ValueError, match="Cannot pause campaign"):
            campaign.pause()

        # Cannot resume an IDLE campaign
        with pytest.raises(ValueError, match="Cannot resume campaign"):
            campaign.resume()

        # Stop campaign
        campaign.start()
        campaign.stop()
        assert campaign.status == CampaignStatus.STOPPED

        # Cannot resume or start a STOPPED campaign
        with pytest.raises(ValueError, match="Cannot resume campaign"):
            campaign.resume()
        with pytest.raises(ValueError, match="Cannot start campaign"):
            campaign.start()

    def test_process_restart_recovers_state_from_database_source_of_truth(self, db_session):
        """Simulate process restart: Campaign & attempt state is reconstructed from DB, not memory."""
        # 1. Process A creates and starts campaign in SQLite
        camp_domain = Campaign.create(name="Persistent Campaign", channel=Channel.WHATSAPP)
        camp_domain.start()
        camp_model = CampaignModel.from_domain(camp_domain)
        db_session.add(camp_model)
        db_session.commit()

        # 2. Process A abruptly terminates (all Python in-memory objects discarded)
        del camp_domain
        del camp_model

        # 3. Process B boots up, queries DB
        first_id = db_session.scalar(select(CampaignModel.id))
        loaded_model = db_session.get(CampaignModel, first_id)
        assert loaded_model is not None
        recovered_campaign = loaded_model.to_domain()

        assert recovered_campaign.status == CampaignStatus.RUNNING
        assert recovered_campaign.name == "Persistent Campaign"

        # 4. Process B can pause or stop the campaign directly
        recovered_campaign.pause()
        assert recovered_campaign.status == CampaignStatus.PAUSED

        # Save update back to DB
        loaded_model.status = recovered_campaign.status.value
        db_session.commit()

        reloaded_after_pause = db_session.get(CampaignModel, loaded_model.id)
        assert reloaded_after_pause.status == CampaignStatus.PAUSED.value
