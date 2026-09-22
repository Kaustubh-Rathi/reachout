"""Campaign persistence and restart-recovery tests.

Verifies that campaign state is reconstructed from the relational store after a
process restart (the DB is the source of truth, not in-memory state). Campaign
lifecycle transitions themselves are covered by tests/unit/domain/test_campaign.py.
"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.campaign import Campaign
from app.domain.enums import CampaignStatus, Channel
from app.infrastructure.models import CampaignModel


class TestCampaignPersistence:
    """Campaign state round-trips through the relational store."""

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

        # 4. Process B can pause the campaign directly
        recovered_campaign.pause()
        assert recovered_campaign.status == CampaignStatus.PAUSED

        # Save update back to DB
        loaded_model.status = recovered_campaign.status.value
        db_session.commit()

        reloaded_after_pause = db_session.get(CampaignModel, loaded_model.id)
        assert reloaded_after_pause.status == CampaignStatus.PAUSED.value
