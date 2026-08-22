"""SQLite / SQLAlchemy implementation of CampaignRepository port."""

from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy import func, select, update
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

    def _increment_metadata_int(self, campaign_id: str, key: str, delta: int) -> None:
        """Atomically increment an integer key inside metadata_json.

        Uses SQLite json_set/json_extract so concurrent workers/scheduler saves
        do not clobber one another's keys (the whole-blob read-modify-write race).
        """
        self.session.execute(
            update(CampaignModel)
            .where(CampaignModel.id == campaign_id)
            .values(
                metadata_json=func.json_set(
                    CampaignModel.metadata_json,
                    f"$.{key}",
                    func.coalesce(
                        func.json_extract(CampaignModel.metadata_json, f"$.{key}"), 0
                    )
                    + delta,
                )
            )
        )
        self.session.flush()

    def increment_automatic_used(self, campaign_id: str, delta: int = 1) -> None:
        """Atomically increment the campaign's automatic quota usage."""
        self._increment_metadata_int(campaign_id, "automatic_used", delta)

    def increment_manual_used(self, campaign_id: str, delta: int = 1) -> None:
        """Atomically increment the campaign's manual quota usage."""
        self._increment_metadata_int(campaign_id, "manual_used", delta)

    def set_rotation_state(self, campaign_id: str, rotation_state: dict) -> None:
        """Atomically write the rotation_state key inside metadata_json.

        Only touches the `rotation_state` key, leaving quota counters and other
        metadata untouched, so a scheduler save cannot clobber a worker's quota
        accounting (and vice-versa).
        """
        self.session.execute(
            update(CampaignModel)
            .where(CampaignModel.id == campaign_id)
            .values(
                metadata_json=func.json_set(
                    CampaignModel.metadata_json,
                    "$.rotation_state",
                    func.json(json.dumps(rotation_state, default=str)),
                )
            )
        )
        self.session.flush()

    def set_status(self, campaign_id: str, status, ended_at=None) -> None:
        """Atomically update only the status (and optional ended_at) columns.

        Does not touch metadata_json, so control-plane status changes (pause /
        resume / stop) can never clobber the worker's quota counters.
        """
        self.session.execute(
            update(CampaignModel)
            .where(CampaignModel.id == campaign_id)
            .values(
                status=status.value if hasattr(status, "value") else str(status),
                ended_at=ended_at,
            )
        )
        self.session.flush()
