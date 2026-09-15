"""Repository port interfaces for the Reachout platform.

Defined using typing.Protocol to decouple domain logic from persistence mechanisms
(e.g., in-memory dictionaries, JSON files, SQLite, PostgreSQL).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import Channel, OutreachStatus
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.reminder import FollowUpReminder
from app.domain.sender_account import SenderAccount


@runtime_checkable
class ContactRepository(Protocol):
    """Port for persisting and querying Contact domain entities."""

    def get_by_id(self, contact_id: str) -> Optional[Contact]:
        """Retrieve a contact by unique ID."""
        ...

    def get_by_key(self, canonical_key: str) -> Optional[Contact]:
        """Retrieve a contact by canonical composite key (company|phone_or_email)."""
        ...

    def list_all(self) -> List[Contact]:
        """Return all stored contacts."""
        ...

    def find_by_company(self, company_id: str) -> List[Contact]:
        """List all contacts associated with a specific company ID."""
        ...

    def save(self, contact: Contact) -> Contact:
        """Upsert a single contact."""
        ...

    def save_bulk(self, contacts: Sequence[Contact]) -> List[Contact]:
        """Upsert multiple contacts atomically or in batch."""
        ...

    def delete(self, contact_id: str) -> bool:
        """Delete a contact by ID, returning whether a record was removed."""
        ...


@runtime_checkable
class CompanyRepository(Protocol):
    """Port for persisting and querying Company domain entities."""

    def get_by_id(self, company_id: str) -> Optional[Company]:
        """Retrieve a company by unique ID."""
        ...

    def get_by_normalized_name(self, normalized_name: str) -> Optional[Company]:
        """Retrieve a company by normalized name for deduplication."""
        ...

    def list_all(self) -> List[Company]:
        """Return all stored companies."""
        ...

    def save(self, company: Company) -> Company:
        """Upsert a company entity."""
        ...


@runtime_checkable
class CampaignRepository(Protocol):
    """Port for persisting and querying Campaign domain entities."""

    def get_by_id(self, campaign_id: str) -> Optional[Campaign]:
        """Retrieve a campaign by ID."""
        ...

    def list_all(self) -> List[Campaign]:
        """List all campaigns."""
        ...

    def save(self, campaign: Campaign) -> Campaign:
        """Upsert a campaign entity."""
        ...


@runtime_checkable
class OutreachRepository(Protocol):
    """Port for recording and querying OutreachAttempt audit histories."""

    def get_by_id(self, attempt_id: str) -> Optional[OutreachAttempt]:
        """Retrieve a specific attempt by ID."""
        ...

    def get_by_idempotency_key(self, key: str) -> Optional[OutreachAttempt]:
        """Find an existing attempt by its idempotency key to prevent duplicates."""
        ...

    def list_by_contact(self, contact_id: str) -> List[OutreachAttempt]:
        """Return all historical attempts for a given contact."""
        ...

    def list_by_campaign(self, campaign_id: str) -> List[OutreachAttempt]:
        """Return all attempts generated within a given campaign."""
        ...

    def list_by_status(self, status: OutreachStatus) -> List[OutreachAttempt]:
        """Return all attempts currently in a given status (e.g. RECOVERY_REQUIRED)."""
        ...

    def save(self, attempt: OutreachAttempt) -> OutreachAttempt:
        """Save/upsert an outreach attempt record."""
        ...


@runtime_checkable
class SenderRepository(Protocol):
    """Port for managing SenderAccount identities."""

    def get_by_id(self, sender_id: str) -> Optional[SenderAccount]:
        """Retrieve a sender account by ID."""
        ...

    def list_by_channel(self, channel: Channel) -> List[SenderAccount]:
        """List all sender accounts configured for a given channel."""
        ...

    def list_active(self, channel: Optional[Channel] = None) -> List[SenderAccount]:
        """List active and operational sender accounts, optionally filtered by channel."""
        ...

    def save(self, sender: SenderAccount) -> SenderAccount:
        """Upsert a sender account entity."""
        ...


@runtime_checkable
class TemplateRepository(Protocol):
    """Port for managing MessageTemplate copies."""

    def get_by_id(self, template_id: str) -> Optional[MessageTemplate]:
        """Retrieve a template by ID."""
        ...

    def list_by_channel(self, channel: Channel) -> List[MessageTemplate]:
        """List all message templates available for a given channel."""
        ...

    def save(self, template: MessageTemplate) -> MessageTemplate:
        """Upsert a message template."""
        ...


@runtime_checkable
class ReminderRepository(Protocol):
    """Port for managing FollowUpReminder entities."""

    def get_by_id(self, reminder_id: str) -> Optional[FollowUpReminder]:
        """Retrieve a reminder by ID."""
        ...

    def list_due(self, current_time: datetime) -> List[FollowUpReminder]:
        """List all pending reminders that are due at or before current_time."""
        ...

    def list_by_contact(self, contact_id: str) -> List[FollowUpReminder]:
        """List all reminders for a given contact."""
        ...

    def save(self, reminder: FollowUpReminder) -> FollowUpReminder:
        """Upsert a reminder."""
        ...
