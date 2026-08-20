"""Pure domain enums for the Reachout CRM and Outreach engine.

These enums define canonical, uncoupled domain states for channels, campaigns,
outreach attempts, CRM outcomes, interview states, and sender account statuses.
"""

from __future__ import annotations

from enum import Enum, unique


@unique
class Channel(str, Enum):
    """Supported outreach communication channels."""
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"

    def __str__(self) -> str:
        return self.value


@unique
class CampaignStatus(str, Enum):
    """Lifecycle states of an outreach campaign."""
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    def __str__(self) -> str:
        return self.value

    @property
    def is_active(self) -> bool:
        return self in (CampaignStatus.STARTING, CampaignStatus.RUNNING)

    @property
    def is_terminal(self) -> bool:
        return self in (CampaignStatus.STOPPED, CampaignStatus.COMPLETED, CampaignStatus.FAILED)


@unique
class OutreachStatus(str, Enum):
    """Lifecycle and execution states for an individual outreach attempt.

    Note on external side effects:
    When message delivery state cannot be confirmed due to process crash, network
    interruption, or provider timeout, state transitions to UNKNOWN or
    RECOVERY_REQUIRED instead of assuming failure or success.
    """
    PREPARED = "PREPARED"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

    def __str__(self) -> str:
        return self.value

    @property
    def is_successful(self) -> bool:
        return self == OutreachStatus.SENT

    @property
    def is_in_flight(self) -> bool:
        return self in (OutreachStatus.QUEUED, OutreachStatus.SENDING)

    @property
    def requires_attention(self) -> bool:
        return self in (OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED)

    @property
    def is_final(self) -> bool:
        return self in (OutreachStatus.SENT, OutreachStatus.FAILED)


@unique
class AttemptType(str, Enum):
    """Classification of how an outreach attempt was initiated."""
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    RESEND = "RESEND"

    def __str__(self) -> str:
        return self.value


@unique
class CRMOutcome(str, Enum):
    """High-level CRM response classification."""
    NONE = "NONE"
    NOT_CONTACTED = "NOT_CONTACTED"
    CONTACTED = "CONTACTED"
    PENDING_REPLY = "PENDING_REPLY"
    REPLIED = "REPLIED"
    FOLLOW_UP = "FOLLOW_UP"
    INTERESTED = "INTERESTED"
    NOT_INTERESTED = "NOT_INTERESTED"
    REPLIED_NO_OPENINGS = "REPLIED_NO_OPENINGS"
    REFERRAL_GIVEN = "REFERRAL_GIVEN"
    NOT_HIRING_FRESHERS = "NOT_HIRING_FRESHERS"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    CLOSED = "CLOSED"
    GHOSTED = "GHOSTED"

    def __str__(self) -> str:
        return self.value

    @property
    def is_positive(self) -> bool:
        return self in (
            CRMOutcome.INTERESTED,
            CRMOutcome.OFFER,
            CRMOutcome.REFERRAL_GIVEN,
        )

    @property
    def is_replied(self) -> bool:
        return self not in (
            CRMOutcome.NONE,
            CRMOutcome.NOT_CONTACTED,
            CRMOutcome.CONTACTED,
            CRMOutcome.PENDING_REPLY,
            CRMOutcome.GHOSTED,
        )


@unique
class InterviewState(str, Enum):
    """Granular interview tracking states for interested candidates."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    INTERVIEW = "INTERVIEW"
    NOT_INTERVIEW = "NOT_INTERVIEW"

    def __str__(self) -> str:
        return self.value


@unique
class CompanyStatus(str, Enum):
    """Company-level aggregate outreach and engagement lifecycle status."""
    NOT_CONTACTED = "NOT_CONTACTED"
    IN_PROGRESS = "IN_PROGRESS"
    CONTACTED = "CONTACTED"
    CLOSED = "CLOSED"

    def __str__(self) -> str:
        return self.value


@unique
class SenderStatus(str, Enum):
    """Operational status of a sender account."""
    NOT_CONFIGURED = "NOT_CONFIGURED"
    QR_REQUIRED = "QR_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RATE_LIMITED = "RATE_LIMITED"
    DISCONNECTED = "DISCONNECTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SUSPENDED = "SUSPENDED"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value

    @property
    def is_usable(self) -> bool:
        return self == SenderStatus.ACTIVE


@unique
class ReminderStatus(str, Enum):
    """Status of a follow-up reminder."""
    PENDING = "PENDING"
    DISMISSED = "DISMISSED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"

    def __str__(self) -> str:
        return self.value
