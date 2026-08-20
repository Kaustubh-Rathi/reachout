"""In-memory domain event bus and broadcaster.

Implements the EventPublisher port to broadcast real-time lifecycle events
(campaigns, attempts, senders, reminders) to listeners and UI/SSE subscribers.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional, Sequence

from app.ports.infrastructure import DomainEvent, EventPublisher

EventListener = Callable[[DomainEvent], None]


class EventBus:
    """Thread-safe domain event publisher with in-memory event buffer and subscriber registry."""

    def __init__(self, max_buffer_size: int = 1000) -> None:
        self._lock = threading.RLock()
        self._listeners: List[EventListener] = []
        self._typed_listeners: Dict[str, List[EventListener]] = {}
        self._recent_events: Deque[DomainEvent] = deque(maxlen=max_buffer_size)

    def subscribe(self, listener: EventListener, event_type: Optional[str] = None) -> None:
        """Register a listener for all events or a specific event type."""
        with self._lock:
            if event_type:
                if event_type not in self._typed_listeners:
                    self._typed_listeners[event_type] = []
                self._typed_listeners[event_type].append(listener)
            else:
                self._listeners.append(listener)

    def unsubscribe(self, listener: EventListener, event_type: Optional[str] = None) -> None:
        """Remove a previously registered listener."""
        with self._lock:
            if event_type and event_type in self._typed_listeners:
                self._typed_listeners[event_type] = [l for l in self._typed_listeners[event_type] if l != listener]
            else:
                self._listeners = [l for l in self._listeners if l != listener]

    def publish(self, event: DomainEvent) -> None:
        """Publish a single domain event to all matching listeners."""
        with self._lock:
            self._recent_events.append(event)
            all_target_listeners = list(self._listeners)
            if event.event_type in self._typed_listeners:
                all_target_listeners.extend(self._typed_listeners[event.event_type])

        for listener in all_target_listeners:
            try:
                listener(event)
            except Exception as exc:
                print(f"[EventBus] Error in event listener {listener}: {exc}")

    def publish_batch(self, events: Sequence[DomainEvent]) -> None:
        """Publish a sequence of events."""
        for evt in events:
            self.publish(evt)

    def get_recent_events(self, limit: int = 100) -> List[DomainEvent]:
        """Retrieve recent events from in-memory ring buffer."""
        with self._lock:
            return list(self._recent_events)[-limit:]

    def clear(self) -> None:
        """Clear all listeners and history."""
        with self._lock:
            self._listeners.clear()
            self._typed_listeners.clear()
            self._recent_events.clear()


# Global default event bus instance
default_event_bus = EventBus()

# Event mapping to standard Phase 8 event types
_EVENT_TYPE_ALIASES = {
    "CampaignStarted": ["CAMPAIGN_STARTED"],
    "CampaignPaused": ["CAMPAIGN_PAUSED"],
    "CampaignResumed": ["CAMPAIGN_RESUMED"],
    "CampaignCompleted": ["CAMPAIGN_COMPLETED"],
    "CampaignStopped": ["CAMPAIGN_STOPPED"],
    "CampaignFailed": ["CAMPAIGN_FAILED"],
    "AttemptPrepared": ["OUTREACH_PREPARED", "MESSAGE_PREPARED"],
    "AttemptStarted": ["OUTREACH_STARTED", "MESSAGE_STARTED"],
    "AttemptSent": ["OUTREACH_SENT", "MESSAGE_SENT"],
    "AttemptFailed": ["OUTREACH_FAILED", "MESSAGE_FAILED"],
    "AttemptUnknown": ["OUTREACH_RECOVERY_REQUIRED"],
    "AttemptRecoveryRequired": ["OUTREACH_RECOVERY_REQUIRED"],
}


def _forward_to_service_bus(event: DomainEvent) -> None:
    try:
        from app.services.event_bus import event_bus

        # Forward base event
        event_bus.publish(event)

        # Forward aliases if present
        aliases = _EVENT_TYPE_ALIASES.get(event.event_type, [])
        for alias in aliases:
            if alias != event.event_type:
                alias_event = DomainEvent(
                    event_type=alias,
                    payload=event.payload,
                    occurred_at=event.occurred_at,
                    event_id=event.event_id,
                )
                event_bus.publish(alias_event)
    except Exception:
        pass


default_event_bus.subscribe(_forward_to_service_bus)

