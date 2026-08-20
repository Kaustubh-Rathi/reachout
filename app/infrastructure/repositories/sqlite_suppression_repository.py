"""SQLite / SQLAlchemy implementation of suppression/tombstone records repository."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import SuppressionRecordModel


class SqliteSuppressionRepository:
    """Repository managing suppressed/tombstoned entities so deleted contacts are not resurrected by synchronizer."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def is_suppressed(
        self,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        canonical_key: Optional[str] = None,
    ) -> bool:
        """Check if a phone, email, or canonical key is suppressed."""
        identifiers = []
        if phone:
            identifiers.append(phone.strip())
        if email:
            identifiers.append(email.strip().lower())
        if canonical_key:
            identifiers.append(canonical_key.strip().lower())

        if not identifiers:
            return False

        stmt = select(SuppressionRecordModel).where(SuppressionRecordModel.identifier.in_(identifiers)).limit(1)
        return self.session.scalars(stmt).first() is not None

    def add_suppression(
        self,
        suppression_type: str,
        identifier: str,
        reason: str = "MANUAL_CRM_DELETION",
        timestamp: Optional[datetime] = None,
    ) -> SuppressionRecordModel:
        """Record a tombstone suppression."""
        clean_id = identifier.strip()
        if suppression_type in ("EMAIL", "CANONICAL_KEY"):
            clean_id = clean_id.lower()

        stmt = select(SuppressionRecordModel).where(
            SuppressionRecordModel.suppression_type == suppression_type,
            SuppressionRecordModel.identifier == clean_id,
        ).limit(1)
        existing = self.session.scalars(stmt).first()
        if existing:
            return existing

        now = timestamp or datetime.now(timezone.utc)
        record = SuppressionRecordModel(
            id=f"sup_{uuid.uuid4().hex[:16]}",
            suppression_type=suppression_type,
            identifier=clean_id,
            reason=reason,
            created_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_all(self) -> List[SuppressionRecordModel]:
        stmt = select(SuppressionRecordModel).order_by(SuppressionRecordModel.created_at.desc())
        return list(self.session.scalars(stmt).all())
