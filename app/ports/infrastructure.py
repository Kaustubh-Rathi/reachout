"""Infrastructure and utility port interfaces.

Includes time abstraction (Clock), job scheduling (Scheduler), and event
broadcasting (EventPublisher) to ensure complete testability and inversion of control.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Protocol, Sequence, runtime_checkable


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

    def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish multiple domain events."""
        ...

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Construct and publish a single domain event from a name and payload."""
        ...


@runtime_checkable
class Scheduler(Protocol):
    """Port for background job and delay scheduling."""

    def schedule(self, task_id: str, run_at: datetime, action: Callable[[], Any]) -> None:
        """Schedule an action for execution at a specific datetime."""
        ...

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending scheduled task."""
        ...
