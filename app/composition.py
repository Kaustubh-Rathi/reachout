"""Process-wide composition root.

Owns the long-lived adapter singletons (event bus, pacing, sessions, vault) so
infrastructure modules do not hide process-level globals. Tests inject fakes
directly; code that needs the canonical process instance resolves it here.
"""

from __future__ import annotations

from functools import lru_cache

from app.infrastructure.events.event_bus import EventBus


@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    """Return the process-wide canonical event bus."""
    return EventBus()
