"""SQLite / SQLAlchemy implementation of OutreachRepository port."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.infrastructure.models import OutreachAttemptModel
from app.ports.repositories import OutreachRepository


class SqliteOutreachRepository(OutreachRepository):
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
        status_val = status.value
        stmt = (
            select(OutreachAttemptModel)
            .where(OutreachAttemptModel.status == status_val)
            .order_by(OutreachAttemptModel.prepared_at.asc())
        )
        models = self.session.scalars(stmt).all()
        return [m.to_domain() for m in models]

    def iter_by_status(
        self,
        status: OutreachStatus,
        batch_size: int = 200,
        failure_detail_limit: Optional[int] = None,
    ) -> Iterable[OutreachAttempt]:
        ordering = (
            OutreachAttemptModel.completed_at.desc(),
            OutreachAttemptModel.prepared_at.desc(),
            OutreachAttemptModel.id.desc(),
        )
        if failure_detail_limit is None:
            stmt = (
                select(OutreachAttemptModel)
                .where(OutreachAttemptModel.status == status.value)
                .order_by(*ordering)
                .execution_options(yield_per=batch_size)
            )
            for model in self.session.scalars(stmt):
                yield model.to_domain()
            return

        failure_detail = func.substr(
            OutreachAttemptModel.failure_detail,
            1,
            failure_detail_limit,
        ).label("failure_detail")
        stmt = (
            select(
                OutreachAttemptModel.id,
                OutreachAttemptModel.contact_id,
                OutreachAttemptModel.sender_account_id,
                OutreachAttemptModel.channel,
                OutreachAttemptModel.attempt_type,
                OutreachAttemptModel.status,
                OutreachAttemptModel.idempotency_key,
                OutreachAttemptModel.message_body_snapshot,
                OutreachAttemptModel.destination,
                OutreachAttemptModel.campaign_id,
                OutreachAttemptModel.template_id,
                OutreachAttemptModel.subject_snapshot,
                OutreachAttemptModel.attachment_snapshot,
                OutreachAttemptModel.prepared_at,
                OutreachAttemptModel.started_at,
                OutreachAttemptModel.completed_at,
                OutreachAttemptModel.failure_code,
                failure_detail,
                OutreachAttemptModel.provider_reference,
                OutreachAttemptModel.recovery_notes,
            )
            .where(OutreachAttemptModel.status == status.value)
            .order_by(*ordering)
            .execution_options(yield_per=batch_size)
        )
        for row in self.session.execute(stmt):
            yield OutreachAttempt(
                id=row.id,
                contact_id=row.contact_id,
                sender_account_id=row.sender_account_id,
                channel=Channel(row.channel),
                attempt_type=AttemptType(row.attempt_type),
                status=OutreachStatus(row.status),
                idempotency_key=row.idempotency_key,
                message_body_snapshot=row.message_body_snapshot,
                destination=row.destination,
                campaign_id=row.campaign_id,
                template_id=row.template_id,
                subject_snapshot=row.subject_snapshot,
                attachment_snapshot=row.attachment_snapshot,
                prepared_at=row.prepared_at,
                started_at=row.started_at,
                completed_at=row.completed_at,
                failure_code=row.failure_code,
                failure_detail=row.failure_detail,
                provider_reference=row.provider_reference,
                recovery_notes=row.recovery_notes,
            )

    def mark_recovery_sent_if_unresolved(
        self,
        attempt_id: str,
        completed_at: datetime,
        recovery_notes: str,
    ) -> Optional[OutreachAttempt]:
        result = self.session.execute(
            update(OutreachAttemptModel)
            .where(
                OutreachAttemptModel.id == attempt_id,
                OutreachAttemptModel.status.in_([OutreachStatus.UNKNOWN.value, OutreachStatus.RECOVERY_REQUIRED.value]),
            )
            .values(
                status=OutreachStatus.SENT.value,
                completed_at=completed_at,
                recovery_notes=recovery_notes,
            )
        )
        if result.rowcount != 1:
            return None
        self.session.flush()
        model = self.session.get(OutreachAttemptModel, attempt_id)
        return model.to_domain() if model else None

    def fail_recovery_if_unresolved(
        self,
        attempt_id: str,
        completed_at: datetime,
        recovery_notes: str,
        failure_code: str,
        failure_detail: str,
    ) -> Optional[OutreachAttempt]:
        result = self.session.execute(
            update(OutreachAttemptModel)
            .where(
                OutreachAttemptModel.id == attempt_id,
                OutreachAttemptModel.status.in_([OutreachStatus.UNKNOWN.value, OutreachStatus.RECOVERY_REQUIRED.value]),
            )
            .values(
                status=OutreachStatus.FAILED.value,
                completed_at=completed_at,
                recovery_notes=recovery_notes,
                failure_code=failure_code,
                failure_detail=failure_detail,
            )
        )
        if result.rowcount != 1:
            return None
        self.session.flush()
        model = self.session.get(OutreachAttemptModel, attempt_id)
        return model.to_domain() if model else None

    def save(self, attempt: OutreachAttempt) -> OutreachAttempt:
        existing = self.session.get(OutreachAttemptModel, attempt.id)
        if existing:
            existing.status = attempt.status.value
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
