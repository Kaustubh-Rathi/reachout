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
from typing import Any, AsyncGenerator, Callable, Deque, Dict, List, Optional

from app.ports.infrastructure import DomainEvent

EventListener = Callable[[DomainEvent], None]


class EventBus:
    """Thread-safe and async-compatible domain event publisher and broker."""

    def __init__(self, max_buffer_size: int = 1000) -> None:
        self._lock = threading.RLock()
        self._listeners: List[EventListener] = []
        self._typed_listeners: Dict[str, List[EventListener]] = {}
        # Map each subscriber queue to the event loop that owns it. ``asyncio.Queue``
        # is not thread-safe, so publishers running on worker/threadpool threads must
        # hand events to the owning loop via ``call_soon_threadsafe``.
        self._async_subscribers: Dict[asyncio.Queue[DomainEvent], asyncio.AbstractEventLoop] = {}
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
                self._typed_listeners[event_type] = [x for x in self._typed_listeners[event_type] if x != listener]
            else:
                self._listeners = [x for x in self._listeners if x != listener]

    async def subscribe_async(self) -> AsyncGenerator[DomainEvent, None]:
        """Subscribe to real-time events as an async generator for WebSocket/SSE."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._async_subscribers[queue] = loop
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            with self._lock:
                self._async_subscribers.pop(queue, None)

    @staticmethod
    def _deliver(queue: asyncio.Queue[DomainEvent], event: DomainEvent) -> None:
        """Run on the subscriber's event loop; never touches shared bus state."""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer: drop the oldest buffered event and keep the stream
            # alive rather than silently disconnecting the subscriber.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event to all synchronous listeners and async subscriber queues."""
        with self._lock:
            self._recent_events.append(event)
            all_target_listeners = list(self._listeners)
            if event.event_type in self._typed_listeners:
                all_target_listeners.extend(self._typed_listeners[event.event_type])

            # Dispatch to async subscriber queues. Publishers may run on worker or
            # threadpool threads, so hop onto each subscriber's owning loop instead
            # of mutating its asyncio.Queue from the wrong thread.
            dead_queues = []
            for queue, loop in list(self._async_subscribers.items()):
                if loop.is_closed():
                    dead_queues.append(queue)
                    continue
                try:
                    loop.call_soon_threadsafe(self._deliver, queue, event)
                except RuntimeError:
                    dead_queues.append(queue)
            for dead in dead_queues:
                self._async_subscribers.pop(dead, None)

        # Invoke synchronous callbacks outside lock to avoid deadlocks
        for listener in all_target_listeners:
            try:
                listener(event)
            except Exception as exc:
                print(f"[EventBus] Error in event listener {listener}: {exc}")

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Convenience helper to construct and broadcast a single domain event."""
        event = DomainEvent(
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc),
        )
        self.publish(event)
        return event

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


# Canonical singleton event bus instance
default_event_bus = EventBus()
event_bus = default_event_bus
