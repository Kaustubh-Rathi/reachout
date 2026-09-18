"""Suppression / tombstone domain model.

Records identifiers (phone, email, canonical key) that must never be
resurrected by source synchronization after an operator deletes a contact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class SuppressionRecord:
    """Immutable tombstone blocking an identifier from being re-ingested."""

    id: str
    suppression_type: str
    identifier: str
    reason: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        suppression_type: str,
        identifier: str,
        reason: str = "MANUAL_CRM_DELETION",
        timestamp: Optional[datetime] = None,
    ) -> SuppressionRecord:
        clean_id = identifier.strip()
        if suppression_type in ("EMAIL", "CANONICAL_KEY"):
            clean_id = clean_id.lower()
        return cls(
            id=f"sup_{uuid.uuid4().hex[:16]}",
            suppression_type=suppression_type,
            identifier=clean_id,
            reason=reason,
            created_at=timestamp or datetime.now(timezone.utc),
        )
