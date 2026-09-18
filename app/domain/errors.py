"""Application/domain error hierarchy.

Typed errors let services express intent without encoding it into message
strings, and let the HTTP layer map failures to status codes in one place.
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """Base class for expected, user-facing application errors."""


class NotFoundError(AppError):
    """A requested entity does not exist."""


class ValidationError(AppError):
    """Input failed a business or domain validation rule."""


class ConflictError(AppError):
    """The request conflicts with the current state of a resource."""


class OutreachNotReadyError(AppError, ValueError):
    """Outreach prerequisites are not satisfied for a campaign/channel.

    Also subclasses ``ValueError`` so existing callers that catch ``ValueError``
    keep working while the HTTP layer uses the structured ``as_detail()`` payload.
    """

    def __init__(self, reason: str, detail: str, context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(f"OUTREACH_NOT_READY: {reason} - {detail}")
        self.reason = reason
        self.detail = detail
        self.context = context or {}

    def as_detail(self) -> dict[str, Any]:
        """Structured error payload consumed by the dashboard readiness modal."""
        return {"error": "OUTREACH_NOT_READY", "reason": self.reason, "message": self.detail}
