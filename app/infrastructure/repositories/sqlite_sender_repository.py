"""SQLite / SQLAlchemy implementation of SenderRepository port."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Channel
from app.domain.sender_account import SenderAccount
from app.infrastructure.models import SenderAccountModel
from app.ports.repositories import SenderRepository


class SqliteSenderRepository(SenderRepository):
    """Repository handling persistence and queries for SenderAccount entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, sender_id: str) -> Optional[SenderAccount]:
        model = self.session.get(SenderAccountModel, sender_id)
        return model.to_domain() if model else None

    def list_by_channel(self, channel: Channel) -> List[SenderAccount]:
        channel_val = channel.value if hasattr(channel, "value") else str(channel)
        stmt = (
            select(SenderAccountModel).where(SenderAccountModel.channel == channel_val).order_by(SenderAccountModel.id)
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_active(self, channel: Optional[Channel] = None) -> List[SenderAccount]:
        stmt = select(SenderAccountModel).where(SenderAccountModel.status == "ACTIVE")
        if channel:
            channel_val = channel.value if hasattr(channel, "value") else str(channel)
            stmt = stmt.where(SenderAccountModel.channel == channel_val)
        stmt = stmt.order_by(SenderAccountModel.id)
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, sender: SenderAccount) -> SenderAccount:
        existing = self.session.get(SenderAccountModel, sender.id)
        if existing:
            existing.channel = sender.channel.value if hasattr(sender.channel, "value") else str(sender.channel)
            existing.provider = sender.provider
            existing.identity = sender.identity
            existing.display_name = sender.display_name
            existing.status = sender.status.value if hasattr(sender.status, "value") else str(sender.status)
            existing.credential_ref = sender.credential_ref
            existing.session_ref = sender.session_ref
            existing.last_used_at = sender.last_used_at
            existing.daily_limit = sender.daily_limit
            existing.hourly_limit = sender.hourly_limit
        else:
            model = SenderAccountModel.from_domain(sender)
            self.session.add(model)
        self.session.flush()
        return sender
