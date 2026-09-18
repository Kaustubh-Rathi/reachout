"""Application/domain error hierarchy.

Typed errors let services express intent without encoding it into message
strings, and let the HTTP layer map failures to status codes in one place.

``AppError`` subclasses ``ValueError`` so existing callers that catch
``ValueError`` keep working while the HTTP layer maps each subtype precisely.
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(ValueError):
    """Base class for expected, user-facing application errors."""


class NotFoundError(AppError):
    """A requested entity does not exist."""


class ValidationError(AppError):
    """Input failed a business or domain validation rule."""


class ConflictError(AppError):
    """The request conflicts with the current state of a resource."""


class ConfigurationError(AppError):
    """Required configuration or credentials are missing or invalid."""


class SourceError(AppError):
    """A source file could not be read or parsed."""


class DataIntegrityError(AppError):
    """Persisted data is corrupt or violates an invariant."""


class AlreadySentError(AppError):
    """An idempotent attempt was already delivered for this endpoint."""

    def __init__(self, attempt_id: str) -> None:
        super().__init__(f"Outreach already sent for this endpoint (attempt {attempt_id})")
        self.attempt_id = attempt_id


class OutreachNotReadyError(AppError):
    """Outreach prerequisites are not satisfied for a campaign/channel."""

    def __init__(self, reason: str, detail: str, context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(f"OUTREACH_NOT_READY: {reason} - {detail}")
        self.reason = reason
        self.detail = detail
        self.context = context or {}

    def as_detail(self) -> dict[str, Any]:
        """Structured error payload consumed by the dashboard readiness modal."""
        return {"error": "OUTREACH_NOT_READY", "reason": self.reason, "message": self.detail}
