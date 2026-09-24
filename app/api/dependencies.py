"""HTTP-layer dependency adapters.

This is the single place where the API layer touches concrete infrastructure.
Routers depend on these adapters (a DB session and the event stream port)
instead of importing adapters directly, so the HTTP boundary stays swappable
and testable.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.composition import get_event_bus
from app.infrastructure.database import get_session as _get_session
from app.ports.infrastructure import EventStream


def get_db_session(session: Session = Depends(_get_session)) -> Session:
    """Yield the request-scoped SQLAlchemy session."""
    return session


def get_event_stream() -> EventStream:
    """Resolve the process-wide event stream (subscription + history) port."""
    return get_event_bus()
