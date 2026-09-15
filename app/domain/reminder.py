"""Follow-Up Reminder domain entity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.domain.enums import ReminderStatus


@dataclass
class FollowUpReminder:
    """Represents a scheduled follow-up reminder for a contact.

    Attributes:
        id: Unique identifier for the reminder.
        contact_id: Target contact entity reference.
        due_at: Timestamp after which the reminder is active/due.
        reason: Description or context triggering the reminder.
        status: Current status (PENDING, DISMISSED, COMPLETED, EXPIRED).
        created_at: Creation timestamp.
        completed_at: Timestamp when reminder was acted upon or dismissed.
    """

    id: str
    contact_id: str
    due_at: datetime
    reason: str
    status: ReminderStatus = ReminderStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"rem_{uuid.uuid4().hex[:12]}"

    @classmethod
    def create(
        cls,
        contact_id: str,
        due_at: datetime,
        reason: str = "Follow-up needed for interested contact",
        reminder_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> FollowUpReminder:
        rid = reminder_id or f"rem_{uuid.uuid4().hex[:12]}"
        now = created_at or datetime.now(timezone.utc)
        return cls(
            id=rid,
            contact_id=contact_id,
            due_at=due_at,
            reason=reason,
            status=ReminderStatus.PENDING,
            created_at=now,
        )

    def is_due(self, current_time: Optional[datetime] = None) -> bool:
        """Check if reminder is currently pending and due."""
        if self.status != ReminderStatus.PENDING:
            return False
        now = current_time or datetime.now(timezone.utc)
        return now >= self.due_at

    def mark_completed(self, timestamp: Optional[datetime] = None) -> None:
        """Mark reminder as completed."""
        self.status = ReminderStatus.COMPLETED
        self.completed_at = timestamp or datetime.now(timezone.utc)

    def complete(self, timestamp: Optional[datetime] = None) -> None:
        """Mark reminder as completed (alias for mark_completed)."""
        self.mark_completed(timestamp)
