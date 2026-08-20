"""Real-time event broadcasting and domain event bus re-export.

Canonical implementation lives in app.infrastructure.events.event_bus.
"""

from __future__ import annotations

from app.infrastructure.events.event_bus import (
    EventBus,
    InMemoryEventBus,
    default_event_bus,
    event_bus,
)

__all__ = ["EventBus", "InMemoryEventBus", "default_event_bus", "event_bus"]
