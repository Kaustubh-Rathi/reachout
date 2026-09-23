"""SQLite / SQLAlchemy implementation of TemplateRepository port."""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate
from app.infrastructure.models import MessageTemplateModel
from app.infrastructure.providers.attachments import resolve_attachment_path
from app.ports.repositories import TemplateRepository

logger = logging.getLogger(__name__)


class SqliteTemplateRepository(TemplateRepository):
    """Repository handling persistence and queries for MessageTemplate entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, template_id: str) -> Optional[MessageTemplate]:
        model = self.session.get(MessageTemplateModel, template_id)
        return model.to_domain() if model else None

    def list_by_channel(self, channel: Channel, active_only: bool = False) -> List[MessageTemplate]:
        channel_val = channel.value
        stmt = select(MessageTemplateModel).where(MessageTemplateModel.channel == channel_val)
        if active_only:
            stmt = stmt.where(MessageTemplateModel.active)
        stmt = stmt.order_by(MessageTemplateModel.id)
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, template: MessageTemplate) -> MessageTemplate:
        ref = template.attachment_ref
        if ref:
            resolved = resolve_attachment_path(ref)
            if resolved is None or not resolved.exists():
                logger.warning(
                    "Saving template '%s' with unresolvable attachment ref %r; "
                    "dispatches using it will fail until the file exists.",
                    template.id,
                    ref,
                )
        existing = self.session.get(MessageTemplateModel, template.id)
        if existing:
            existing.name = template.name
            existing.channel = template.channel.value
            existing.body = template.body
            existing.subject = template.subject
            existing.attachment_ref = template.attachment_ref
            existing.phone_number = template.phone_number
            existing.active = template.active
            existing.updated_at = template.updated_at
        else:
            model = MessageTemplateModel.from_domain(template)
            self.session.add(model)
        self.session.flush()
        return template
