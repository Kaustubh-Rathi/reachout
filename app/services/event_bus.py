"""Real-time event broadcasting and domain event bus.

Implements EventPublisher port to decouple domain actions from SSE streaming
and real-time notifications.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from app.ports.infrastructure import DomainEvent, EventPublisher


class InMemoryEventBus:
    """Thread-safe and async-friendly in-memory event bus and SSE broker."""

    def __init__(self, max_history: int = 200) -> None:
        self._max_history = max_history
        self._history: deque[DomainEvent] = deque(maxlen=max_history)
        self._subscribers: Set[asyncio.Queue[DomainEvent]] = set()
        self._lock = asyncio.Lock()

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event synchronously or asynchronously."""
        self._history.append(event)
        
        # Dispatch to any active async listener queues
        dead_subscribers = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except Exception:
                dead_subscribers.append(queue)
        
        for dead in dead_subscribers:
            self._subscribers.discard(dead)

    def publish_batch(self, events: list[DomainEvent]) -> None:
        for ev in events:
            self.publish(ev)

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Convenience method to construct and publish a domain event."""
        event = DomainEvent(
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc),
        )
        self.publish(event)
        return event

    async def subscribe(self) -> AsyncGenerator[DomainEvent, None]:
        """Subscribe to real-time events as an async generator for SSE."""
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.discard(queue)

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent events in reverse chronological order."""
        events = list(self._history)
        events.reverse()
        sliced = events[:limit]
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in sliced
        ]


# Singleton global event bus
event_bus = InMemoryEventBus()
