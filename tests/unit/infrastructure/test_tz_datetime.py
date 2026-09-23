"""Unit tests for timezone-aware datetime persistence (TZDateTime)."""

from datetime import datetime, timedelta, timezone

from app.domain.campaign import Campaign
from app.domain.enums import Channel
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository


class TestTZDateTime:
    def test_round_trip_returns_aware_utc(self, db_session):
        repo = SqliteCampaignRepository(db_session)
        camp = Campaign.create(name="tz", channel=Channel.WHATSAPP, campaign_id="cmp_tz_01")
        repo.save(camp)
        db_session.commit()
        db_session.expire_all()

        loaded = repo.get_by_id("cmp_tz_01")
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at.isoformat().endswith("+00:00")

    def test_non_utc_offset_is_normalized_to_utc(self, db_session):
        repo = SqliteCampaignRepository(db_session)
        plus530 = timezone(timedelta(hours=5, minutes=30))
        camp = Campaign.create(name="tz", channel=Channel.WHATSAPP, campaign_id="cmp_tz_02")
        camp.started_at = datetime(2026, 9, 23, 18, 57, 5, tzinfo=plus530)
        repo.save(camp)
        db_session.commit()
        db_session.expire_all()

        loaded = repo.get_by_id("cmp_tz_02")
        assert loaded is not None
        assert loaded.started_at is not None
        assert loaded.started_at == datetime(2026, 9, 23, 13, 27, 5, tzinfo=timezone.utc)
        assert loaded.started_at.isoformat().endswith("+00:00")
