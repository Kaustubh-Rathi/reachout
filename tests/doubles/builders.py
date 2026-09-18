"""Builders that wire infrastructure collaborators with deterministic test doubles.

Production code requires all collaborators to be injected; tests use these helpers
to provide fakes without repeating boilerplate at every call site.
"""

from __future__ import annotations

from typing import Any, Optional

from app.composition import build_repositories
from app.infrastructure.events.event_bus import EventBus
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler
from app.infrastructure.scheduler.campaign_worker import OutreachWorker
from app.infrastructure.scheduler.rate_limiter import RateLimiter
from app.ports.infrastructure import SystemClock
from tests.doubles.fake_providers import FakeEmailProvider, FakeWhatsAppProvider


def fast_rate_limiter() -> RateLimiter:
    """A rate limiter with negligible pacing delays for fast tests."""
    return RateLimiter(default_channel_delay={"WHATSAPP": 0.01, "EMAIL": 0.01})


def build_worker(
    session_factory: Any,
    *,
    whatsapp_provider: Optional[Any] = None,
    email_provider: Optional[Any] = None,
    rate_limiter: Optional[RateLimiter] = None,
    event_publisher: Optional[Any] = None,
) -> OutreachWorker:
    """Construct an OutreachWorker with fakes for any collaborator not supplied."""
    return OutreachWorker(
        session_factory=session_factory,
        whatsapp_provider=whatsapp_provider or FakeWhatsAppProvider(),
        email_provider=email_provider or FakeEmailProvider(),
        rate_limiter=rate_limiter or fast_rate_limiter(),
        event_publisher=event_publisher or EventBus(),
        repository_factory=build_repositories,
        clock=SystemClock(),
    )


def build_scheduler(
    session_factory: Any,
    *,
    worker: Optional[OutreachWorker] = None,
    event_publisher: Optional[Any] = None,
) -> PersistentCampaignScheduler:
    """Construct a PersistentCampaignScheduler with a fake-backed worker by default."""
    return PersistentCampaignScheduler(
        session_factory=session_factory,
        worker=worker or build_worker(session_factory),
        event_publisher=event_publisher or EventBus(),
        repository_factory=build_repositories,
        clock=SystemClock(),
    )
