"""In-memory domain event bus and broadcaster.

Implements the EventPublisher port to broadcast real-time domain lifecycle events
(campaigns, attempts, senders, reminders) to synchronous listeners and asynchronous
subscribers (WebSocket / SSE) without duplicate broadcasting or legacy aliasing.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Deque, Dict, List, Optional, Sequence, Set

from app.ports.infrastructure import DomainEvent, EventPublisher

EventListener = Callable[[DomainEvent], None]


class EventBus:
    """Thread-safe and async-compatible domain event publisher and broker."""

    def __init__(self, max_buffer_size: int = 1000) -> None:
        self._lock = threading.RLock()
        self._listeners: List[EventListener] = []
        self._typed_listeners: Dict[str, List[EventListener]] = {}
        self._async_subscribers: Set[asyncio.Queue[DomainEvent]] = set()
        self._recent_events: Deque[DomainEvent] = deque(maxlen=max_buffer_size)

    def subscribe(self, listener: Optional[EventListener] = None, event_type: Optional[str] = None):
        """Register a synchronous listener or return an async generator if no listener is supplied."""
        if listener is None:
            return self.subscribe_async()

        with self._lock:
            if event_type:
                if event_type not in self._typed_listeners:
                    self._typed_listeners[event_type] = []
                self._typed_listeners[event_type].append(listener)
            else:
                self._listeners.append(listener)

    def unsubscribe(self, listener: EventListener, event_type: Optional[str] = None) -> None:
        """Remove a previously registered synchronous listener."""
        with self._lock:
            if event_type and event_type in self._typed_listeners:
                self._typed_listeners[event_type] = [l for l in self._typed_listeners[event_type] if l != listener]
            else:
                self._listeners = [l for l in self._listeners if l != listener]

    async def subscribe_async(self) -> AsyncGenerator[DomainEvent, None]:
        """Subscribe to real-time events as an async generator for WebSocket/SSE."""
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._async_subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            with self._lock:
                self._async_subscribers.discard(queue)

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event to all synchronous listeners and async subscriber queues."""
        with self._lock:
            self._recent_events.append(event)
            all_target_listeners = list(self._listeners)
            if event.event_type in self._typed_listeners:
                all_target_listeners.extend(self._typed_listeners[event.event_type])

            # Dispatch to async subscriber queues
            dead_queues = []
            for queue in list(self._async_subscribers):
                try:
                    queue.put_nowait(event)
                except Exception:
                    dead_queues.append(queue)
            for dead in dead_queues:
                self._async_subscribers.discard(dead)

        # Invoke synchronous callbacks outside lock to avoid deadlocks
        for listener in all_target_listeners:
            try:
                listener(event)
            except Exception as exc:
                print(f"[EventBus] Error in event listener {listener}: {exc}")

    def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish a sequence of events."""
        for evt in events:
            self.publish(evt)

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Convenience helper to construct and broadcast a single domain event."""
        event = DomainEvent(
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc),
        )
        self.publish(event)
        return event

    def get_recent_events(self, limit: int = 100) -> List[DomainEvent]:
        """Retrieve recent events from in-memory ring buffer."""
        with self._lock:
            return list(self._recent_events)[-limit:]

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent events in reverse chronological order as dictionaries."""
        with self._lock:
            events = list(self._recent_events)
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

    def clear(self) -> None:
        """Clear all listeners, async subscribers, and event history."""
        with self._lock:
            self._listeners.clear()
            self._typed_listeners.clear()
            self._async_subscribers.clear()
            self._recent_events.clear()


# Canonical singleton event bus instance
default_event_bus = EventBus()
event_bus = default_event_bus
InMemoryEventBus = EventBus
