"""Contact domain entity.

Models individual professional contacts, their CRM outcomes, interview status,
and activity milestones. Message execution history is explicitly separated and
modeled inside OutreachAttempt.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from app.domain.endpoint import (
    CommunicationEndpoint,
    extract_endpoints_from_raw,
    normalize_email_addresses,
    normalize_phone_numbers,
)
from app.domain.enums import Channel, CRMOutcome, InterviewState
from app.domain.source_record import SourceRecord


def extract_first_name(full_name: str) -> str:
    """Extract a polite greeting first name from person name."""
    if not full_name:
        return "there"
    # Remove parenthetical notes
    name = re.sub(r"\([^)]*\)", "", full_name).strip()
    # Strip honorifics
    name = re.sub(r"^(?:mr|mrs|ms|dr|prof)\.?\s+", "", name, flags=re.IGNORECASE)
    parts = name.split()
    if not parts:
        return "there"
    candidate = parts[0].strip()
    if candidate.casefold() in {"there", "na", "n/a", "null", "none"}:
        return "there"
    return candidate.title()


@dataclass
class Contact:
    """Represents an outreach prospect or lead.

    Attributes:
        contact_id: Immutable unique identifier for the contact.
        company_id: Foreign reference to associated Company entity.
        name: Full display name.
        designation: Job title / role at company.
        phone: Raw or comma-separated phone string.
        email: Raw or comma-separated email string.
        source_reference: Provenance tracking to source workbook/sheet/row.
        created_at: Entity creation timestamp.
        updated_at: Entity last-modification timestamp.
        last_whatsapp_at: Timestamp of most recent successful WhatsApp delivery.
        last_email_at: Timestamp of most recent successful Email delivery.
        last_activity_at: Timestamp of any latest outbound/inbound contact activity.
        crm_outcome: High-level CRM outcome classification.
        interview_status: State of interview workflow (for interested contacts).
        interested_at: Timestamp when contact first became interested.
        interview_status_changed_at: Timestamp when interview status last changed.
        notes: User/system notes regarding this contact.
        tags: List of categorization tags.
    """

    contact_id: str
    company_id: str
    name: str
    designation: str = ""
    phone: Optional[str] = None
    email: Optional[str] = None
    source_reference: Optional[SourceRecord] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_whatsapp_at: Optional[datetime] = None
    last_email_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    crm_outcome: CRMOutcome = CRMOutcome.NONE
    interview_status: InterviewState = InterviewState.NOT_APPLICABLE
    interested_at: Optional[datetime] = None
    interview_status_changed_at: Optional[datetime] = None
    notes: str = ""
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.contact_id:
            self.contact_id = f"cnt_{uuid.uuid4().hex[:16]}"
        if self.phone:
            phones = normalize_phone_numbers(self.phone)
            self.phone = ", ".join(phones) if phones else self.phone.strip()
        if self.email:
            emails = normalize_email_addresses(self.email)
            self.email = ", ".join(emails) if emails else self.email.strip().lower()

    @property
    def phones(self) -> List[str]:
        """List of all distinct normalized phone numbers for this contact."""
        return normalize_phone_numbers(self.phone)

    @property
    def emails(self) -> List[str]:
        """List of all distinct normalized email addresses for this contact."""
        return normalize_email_addresses(self.email)

    @property
    def endpoints(self) -> List[CommunicationEndpoint]:
        """Construct all discrete communication endpoints for this contact."""
        return extract_endpoints_from_raw(self.phone, self.email)

    @property
    def primary_phone(self) -> Optional[str]:
        """First normalized phone number or None."""
        p = self.phones
        return p[0] if p else None

    @property
    def primary_email(self) -> Optional[str]:
        """First normalized email address or None."""
        e = self.emails
        return e[0] if e else None

    @property
    def first_name(self) -> str:
        """Derived first name for message personalization."""
        return extract_first_name(self.name)

    @property
    def canonical_key(self) -> str:
        """Deterministic composite key for matching legacy datasets (company_id|phone_or_email)."""
        target = self.primary_phone or self.primary_email or self.contact_id
        return f"{self.company_id.strip().lower()}|{target}"

    def update_crm_outcome(self, outcome: CRMOutcome, timestamp: Optional[datetime] = None) -> None:
        """Transition CRM outcome with automatic interested_at milestone management."""
        now = timestamp or datetime.now(timezone.utc)
        prev = self.crm_outcome
        self.crm_outcome = outcome
        self.updated_at = now
        self.last_activity_at = now

        if outcome == CRMOutcome.INTERESTED and prev != CRMOutcome.INTERESTED:
            if self.interested_at is None:
                self.interested_at = now
            if self.interview_status == InterviewState.NOT_APPLICABLE:
                self.interview_status = InterviewState.PENDING
                self.interview_status_changed_at = now
        elif outcome != CRMOutcome.INTERESTED and prev == CRMOutcome.INTERESTED:
            pass

    def update_interview_status(self, status: InterviewState, timestamp: Optional[datetime] = None) -> None:
        """Transition interview workflow state."""
        now = timestamp or datetime.now(timezone.utc)
        self.interview_status = status
        self.interview_status_changed_at = now
        self.updated_at = now
        self.last_activity_at = now

    def record_outreach_success(self, channel: Channel, timestamp: Optional[datetime] = None) -> None:
        """Record successful message delivery across a specific channel."""
        now = timestamp or datetime.now(timezone.utc)
        if channel == Channel.WHATSAPP:
            self.last_whatsapp_at = now
        elif channel == Channel.EMAIL:
            self.last_email_at = now
        self.last_activity_at = now
        self.updated_at = now
        if self.crm_outcome == CRMOutcome.NONE:
            self.crm_outcome = CRMOutcome.PENDING_REPLY
