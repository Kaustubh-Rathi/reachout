"""Infrastructure and utility port interfaces.

Includes time abstraction (Clock), job scheduling (Scheduler), and event
broadcasting (EventPublisher) to ensure complete testability and inversion of control.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Port for time retrieval to enable deterministic time testing."""

    def now(self) -> datetime:
        """Return current timestamp (always UTC)."""
        ...


class SystemClock:
    """Default system clock implementation returning UTC now."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Test clock implementation frozen at a specific moment or manually advanced."""

    def __init__(self, initial_time: Optional[datetime] = None) -> None:
        self._current_time = initial_time or datetime.now(timezone.utc)

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        self._current_time = new_time

    def advance(self, **delta_kwargs: Any) -> None:
        from datetime import timedelta

        self._current_time += timedelta(**delta_kwargs)


@dataclass(frozen=True)
class DomainEvent:
    """Base domain event envelope."""

    event_type: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@runtime_checkable
class EventPublisher(Protocol):
    """Port for publishing domain events."""

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event."""
        ...

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Construct and publish a single domain event from a name and payload."""
        ...


@runtime_checkable
class CampaignScheduler(Protocol):
    """Port for controlling background campaign execution.

    This is the contract the application layer depends on; the concrete
    PersistentCampaignScheduler adapter implements it.
    """

    def is_running(self, campaign_id: str) -> bool:
        """Return whether a campaign currently has an active execution thread."""
        ...

    def start_campaign(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Begin executing a campaign in the background."""
        ...

    def pause_campaign(self, campaign_id: str) -> None:
        """Request a running campaign to pause."""
        ...

    def resume_campaign(self, campaign_id: str) -> None:
        """Resume a paused campaign."""
        ...

    def stop_campaign(self, campaign_id: str) -> None:
        """Stop a campaign permanently."""
        ...

    def run_crash_recovery_audit(self) -> int:
        """Recover stale in-flight attempts from a previous process and return the count."""
        ...
