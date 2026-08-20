"""SQLite / SQLAlchemy implementation of OutreachRepository port."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.models import OutreachAttemptModel
from app.ports.repositories import OutreachRepository


class SqliteOutreachRepository:
    """Repository handling persistence and queries for OutreachAttempt entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, attempt_id: str) -> Optional[OutreachAttempt]:
        model = self.session.get(OutreachAttemptModel, attempt_id)
        return model.to_domain() if model else None

    def get_by_idempotency_key(self, key: str) -> Optional[OutreachAttempt]:
        stmt = select(OutreachAttemptModel).where(OutreachAttemptModel.idempotency_key == key).limit(1)
        model = self.session.scalars(stmt).first()
        return model.to_domain() if model else None

    def list_all(self) -> List[OutreachAttempt]:
        stmt = select(OutreachAttemptModel).order_by(OutreachAttemptModel.prepared_at.asc())
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_by_contact(self, contact_id: str) -> List[OutreachAttempt]:
        stmt = (
            select(OutreachAttemptModel)
            .where(OutreachAttemptModel.contact_id == contact_id)
            .order_by(OutreachAttemptModel.prepared_at.desc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_by_campaign(self, campaign_id: str) -> List[OutreachAttempt]:
        stmt = (
            select(OutreachAttemptModel)
            .where(OutreachAttemptModel.campaign_id == campaign_id)
            .order_by(OutreachAttemptModel.prepared_at.asc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_by_status(self, status: OutreachStatus) -> List[OutreachAttempt]:
        status_val = status.value if hasattr(status, "value") else str(status)
        stmt = (
            select(OutreachAttemptModel)
            .where(OutreachAttemptModel.status == status_val)
            .order_by(OutreachAttemptModel.prepared_at.asc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def list_by_destination(self, contact_id: str, destination: str) -> List[OutreachAttempt]:
        dest_clean = destination.strip()
        stmt = (
            select(OutreachAttemptModel)
            .where(
                OutreachAttemptModel.contact_id == contact_id,
                OutreachAttemptModel.destination == dest_clean,
            )
            .order_by(OutreachAttemptModel.prepared_at.desc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def save(self, attempt: OutreachAttempt) -> OutreachAttempt:
        existing = self.session.get(OutreachAttemptModel, attempt.id)
        if existing:
            existing.status = attempt.status.value if hasattr(attempt.status, "value") else str(attempt.status)
            existing.destination = attempt.destination
            existing.started_at = attempt.started_at
            existing.completed_at = attempt.completed_at
            existing.failure_code = attempt.failure_code
            existing.failure_detail = attempt.failure_detail
            existing.provider_reference = attempt.provider_reference
            existing.recovery_notes = attempt.recovery_notes
        else:
            model = OutreachAttemptModel.from_domain(attempt)
            self.session.add(model)
        self.session.flush()
        return attempt
