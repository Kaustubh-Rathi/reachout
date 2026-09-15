"""SQLite / SQLAlchemy implementation of ReminderRepository port."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.reminder import FollowUpReminder
from app.infrastructure.models import FollowUpReminderModel
from app.ports.repositories import ReminderRepository


class SqliteReminderRepository(ReminderRepository):
    """Repository handling persistence and queries for FollowUpReminder entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, reminder_id: str) -> Optional[FollowUpReminder]:
        model = self.session.get(FollowUpReminderModel, reminder_id)
        return model.to_domain() if model else None

    def list_due(self, current_time: datetime) -> List[FollowUpReminder]:
        stmt = (
            select(FollowUpReminderModel)
            .where(
                FollowUpReminderModel.status == "PENDING",
                FollowUpReminderModel.due_at <= current_time,
            )
            .order_by(FollowUpReminderModel.due_at.asc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_by_contact(self, contact_id: str) -> List[FollowUpReminder]:
        stmt = (
            select(FollowUpReminderModel)
            .where(FollowUpReminderModel.contact_id == contact_id)
            .order_by(FollowUpReminderModel.due_at.asc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, reminder: FollowUpReminder) -> FollowUpReminder:
        existing = self.session.get(FollowUpReminderModel, reminder.id)
        if existing:
            existing.due_at = reminder.due_at
            existing.reason = reminder.reason
            existing.status = reminder.status.value if hasattr(reminder.status, "value") else str(reminder.status)
            existing.completed_at = reminder.completed_at
        else:
            model = FollowUpReminderModel.from_domain(reminder)
            self.session.add(model)
        self.session.flush()
        return reminder
