"""Infrastructure and utility port interfaces.

Includes time abstraction (Clock), event broadcasting (EventPublisher), live
event streaming (EventStream), background campaign scheduling
(CampaignScheduler), pacing (RateLimiter), WhatsApp session management
(SessionManager), and credential storage (CredentialVault) to ensure complete
testability and inversion of control.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Protocol, runtime_checkable

from app.domain.enums import SenderStatus


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

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> DomainEvent:
        """Construct and publish a single domain event from a name and payload."""
        ...


@runtime_checkable
class EventStream(Protocol):
    """Port for consuming the live event stream (SSE / WebSocket) and its history."""

    def subscribe_async(self) -> AsyncGenerator[DomainEvent, None]:
        """Yield domain events as they are published."""
        ...

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent events in reverse chronological order."""
        ...


@runtime_checkable
class RateLimiter(Protocol):
    """Port for sender-level pacing, concurrency locks, and quota accounting."""

    default_channel_delay: Dict[str, float]

    def wait_for_ready(
        self,
        sender_id: str,
        channel: str,
        daily_limit: Optional[int] = None,
        hourly_limit: Optional[int] = None,
        timeout_seconds: float = 60.0,
        min_delay_override: Optional[float] = None,
    ) -> bool:
        """Block until the sender is ready to dispatch or the timeout expires."""
        ...

    def acquire_sender(self, sender_id: str) -> bool:
        """Reserve a sender identity for exclusive use by an attempt."""
        ...

    def release_sender(self, sender_id: str) -> None:
        """Release a sender lock."""
        ...

    def record_dispatch_success(self, sender_id: str) -> None:
        """Record a successful dispatch completion."""
        ...

    def record_dispatch_failure(self, sender_id: str, is_rate_limit: bool = False) -> float:
        """Record a provider error and return the computed backoff duration."""
        ...

    def reset(self) -> None:
        """Clear all pacing, backoff, and concurrency state."""
        ...


@runtime_checkable
class SessionManager(Protocol):
    """Port for managing WhatsApp authentication state and persisted profiles."""

    def has_persisted_session(self, sender_id: str) -> bool:
        """Return whether an authenticated on-disk profile exists for the sender."""
        ...

    def is_auth_known(self, sender_id: str) -> bool:
        """Return whether the manager holds an in-memory auth state for the sender."""
        ...

    def get_auth_state(self, sender_id: str) -> Dict[str, Any]:
        """Return the current in-memory auth state snapshot."""
        ...

    def set_auth_state(
        self,
        sender_id: str,
        status: SenderStatus,
        qr_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update the in-memory auth state for the sender."""
        ...

    def check_session_status(self, sender_id: str, timeout_seconds: int = 15) -> SenderStatus:
        """Probe a persisted profile in a live browser and return the observed status."""
        ...

    def extract_phone(self, page: Any) -> Optional[str]:
        """Extract the authenticated phone number from a live WhatsApp Web page."""
        ...

    def start_qr_authentication(
        self,
        sender_id: str,
        on_event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        timeout_seconds: int = 120,
        is_temp: bool = False,
        on_resolve: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Begin a QR authentication flow for the sender."""
        ...


@runtime_checkable
class CredentialVault(Protocol):
    """Port for encrypted credential storage keyed by sender account id."""

    def get_credentials(self, sender_account_id: str) -> Optional[Dict[str, str]]:
        """Return stored credentials for a sender, or ``None`` when absent."""
        ...

    def list_senders_with_credentials(self) -> List[str]:
        """List sender account ids that have stored credentials."""
        ...

    def save_credentials(self, sender_account_id: str, creds: Dict[str, str]) -> None:
        """Persist credentials for a sender."""
        ...


@runtime_checkable
class CampaignScheduler(Protocol):
    """Port for controlling background campaign execution.

    This is the contract the application layer depends on; the concrete
    PersistentCampaignScheduler adapter implements it.
    """

    def is_running(self, campaign_id: str) -> bool:
        """Return whether a campaign currently has an active execution thread."""
        ...

    def start_campaign(self, campaign_id: str, max_count: Optional[int] = None) -> None:
        """Begin executing a campaign in the background."""
        ...

    def pause_campaign(self, campaign_id: str) -> None:
        """Request a running campaign to pause."""
        ...

    def resume_campaign(self, campaign_id: str) -> None:
        """Resume a paused campaign."""
        ...

    def run_crash_recovery_audit(self) -> int:
        """Recover stale in-flight attempts from a previous process and return the count."""
        ...
