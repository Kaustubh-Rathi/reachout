"""HTTP-layer dependency adapters.

This is the single place where the API layer touches concrete infrastructure.
Routers depend on these adapters (a DB session, a composed ServiceContext, and
the event publisher port) instead of importing adapters directly, so the HTTP
boundary stays swappable and testable.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.infrastructure.database import get_session as _get_session
from app.infrastructure.events.event_bus import default_event_bus
from app.ports.infrastructure import EventPublisher, EventStream
from app.services.context import ServiceContext, build_service_context


def get_db_session(session: Session = Depends(_get_session)) -> Session:
    """Yield the request-scoped SQLAlchemy session."""
    return session


def get_service_context(session: Session = Depends(_get_session)) -> ServiceContext:
    """Build the port-typed service context for the current request."""
    return build_service_context(session)


def get_event_publisher() -> EventPublisher:
    """Resolve the process-wide event publisher port."""
    return default_event_bus


def get_event_stream() -> EventStream:
    """Resolve the process-wide event stream (subscription + history) port."""
    return default_event_bus
