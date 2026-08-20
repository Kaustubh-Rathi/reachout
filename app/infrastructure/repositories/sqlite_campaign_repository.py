"""SQLite / SQLAlchemy implementation of CampaignRepository port."""

from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.campaign import Campaign
from app.infrastructure.models import CampaignModel
from app.ports.repositories import CampaignRepository


class SqliteCampaignRepository:
    """Repository handling persistence and queries for Campaign entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, campaign_id: str) -> Optional[Campaign]:
        model = self.session.get(CampaignModel, campaign_id)
        return model.to_domain() if model else None

    def list_all(self) -> List[Campaign]:
        stmt = select(CampaignModel).order_by(CampaignModel.created_at.desc())
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, campaign: Campaign) -> Campaign:
        existing = self.session.get(CampaignModel, campaign.id)
        if existing:
            existing.name = campaign.name
            existing.channel = campaign.channel.value if hasattr(campaign.channel, "value") else str(campaign.channel)
            existing.status = campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status)
            existing.template_ids_json = json.dumps(campaign.template_ids or [])
            existing.sender_account_ids_json = json.dumps(campaign.sender_account_ids or [])
            existing.started_at = campaign.started_at
            existing.ended_at = campaign.ended_at
            existing.metadata_json = json.dumps(campaign.metadata or {})
        else:
            model = CampaignModel.from_domain(campaign)
            self.session.add(model)
        self.session.flush()
        return campaign
