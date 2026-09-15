"""SQLAlchemy ORM models representing the Reachout domain schema in relational storage.

All tables include appropriate indexes, constraints, and lossless conversion
methods to/from pure domain entities.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    CRMOutcome,
    InterviewState,
    OutreachStatus,
    ReminderStatus,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.reminder import FollowUpReminder
from app.domain.sender_account import SenderAccount
from app.domain.source_record import SourceRecord
from app.infrastructure.database import Base

logger = logging.getLogger(__name__)


def _coerce_enum(enum_cls, raw, field: str):
    """Strictly coerce a persisted string into its enum value.

    Unknown/corrupt values raise instead of silently defaulting, which would
    hide data corruption and could fail open for security-sensitive fields
    such as sender status.
    """
    try:
        return enum_cls(raw)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Invalid persisted {field}: {raw!r}") from exc


class CompanyModel(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    contacts: Mapped[List[ContactModel]] = relationship(
        "ContactModel", back_populates="company", cascade="all, delete-orphan"
    )

    def to_domain(self) -> Company:
        return Company(
            id=self.id,
            name=self.name,
            normalized_name=self.normalized_name,
            domain=self.domain,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_domain(cls, entity: Company) -> CompanyModel:
        return cls(
            id=entity.id,
            name=entity.name,
            normalized_name=entity.normalized_name,
            domain=entity.domain,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class ContactModel(Base):
    __tablename__ = "contacts"

    contact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    designation: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_whatsapp_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    crm_outcome: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    interview_status: Mapped[str] = mapped_column(String(32), default="NOT_APPLICABLE", nullable=False)
    interested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    interview_status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    # Relationships
    company: Mapped[CompanyModel] = relationship("CompanyModel", back_populates="contacts")
    source_records: Mapped[List[SourceRecordModel]] = relationship(
        "SourceRecordModel", back_populates="contact", cascade="all, delete-orphan"
    )
    outreach_attempts: Mapped[List[OutreachAttemptModel]] = relationship(
        "OutreachAttemptModel", back_populates="contact", cascade="all, delete-orphan"
    )
    reminders: Mapped[List[FollowUpReminderModel]] = relationship(
        "FollowUpReminderModel", back_populates="contact", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_contacts_company_phone", "company_id", "phone"),
        Index("ix_contacts_company_email", "company_id", "email"),
        CheckConstraint(
            "phone IS NOT NULL OR email IS NOT NULL",
            name="ck_contacts_reachable",
        ),
    )

    def to_domain(self) -> Contact:
        tags: List[str] = []
        if self.tags_json:
            try:
                tags = json.loads(self.tags_json)
            except Exception:
                tags = []

        primary_source: Optional[SourceRecord] = None
        if self.source_records:
            primary_source = self.source_records[0].to_domain()

        return Contact(
            contact_id=self.contact_id,
            company_id=self.company_id,
            name=self.name,
            designation=self.designation,
            phone=self.phone,
            email=self.email,
            source_reference=primary_source,
            created_at=self.created_at,
            updated_at=self.updated_at,
            last_whatsapp_at=self.last_whatsapp_at,
            last_email_at=self.last_email_at,
            last_activity_at=self.last_activity_at,
            crm_outcome=_coerce_enum(CRMOutcome, self.crm_outcome, "contact.crm_outcome"),
            interview_status=_coerce_enum(InterviewState, self.interview_status, "contact.interview_status"),
            interested_at=self.interested_at,
            interview_status_changed_at=self.interview_status_changed_at,
            notes=self.notes,
            tags=tags,
        )

    @classmethod
    def from_domain(cls, entity: Contact) -> ContactModel:
        return cls(
            contact_id=entity.contact_id,
            company_id=entity.company_id,
            name=entity.name,
            designation=entity.designation or "",
            phone=entity.phone,
            email=entity.email,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            last_whatsapp_at=entity.last_whatsapp_at,
            last_email_at=entity.last_email_at,
            last_activity_at=entity.last_activity_at,
            crm_outcome=entity.crm_outcome.value
            if isinstance(entity.crm_outcome, CRMOutcome)
            else str(entity.crm_outcome),
            interview_status=entity.interview_status.value
            if isinstance(entity.interview_status, InterviewState)
            else str(entity.interview_status),
            interested_at=entity.interested_at,
            interview_status_changed_at=entity.interview_status_changed_at,
            notes=entity.notes or "",
            tags_json=json.dumps(entity.tags or []),
        )


class SourceRecordModel(Base):
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("contacts.contact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sheet: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    contact: Mapped[ContactModel] = relationship("ContactModel", back_populates="source_records")

    __table_args__ = (Index("ix_source_file_row", "source_file", "source_row"),)

    def to_domain(self) -> SourceRecord:
        return SourceRecord(
            source_file=self.source_file,
            source_sheet=self.source_sheet,
            source_row=self.source_row,
            source_fingerprint=self.source_fingerprint,
            first_seen_at=self.first_seen_at,
            last_seen_at=self.last_seen_at,
        )

    @classmethod
    def from_domain(cls, entity: SourceRecord, contact_id: str, id_override: Optional[str] = None) -> SourceRecordModel:
        import uuid

        rid = id_override or f"src_{uuid.uuid4().hex[:16]}"
        return cls(
            id=rid,
            contact_id=contact_id,
            source_file=entity.source_file,
            source_sheet=entity.source_sheet,
            source_row=entity.source_row,
            source_fingerprint=entity.source_fingerprint,
            first_seen_at=entity.first_seen_at,
            last_seen_at=entity.last_seen_at,
        )


class SenderAccountModel(Base):
    __tablename__ = "sender_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    identity: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    credential_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    session_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    daily_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hourly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # Unique per (channel, identity) EXCEPT empty placeholder identities, which
        # legitimately repeat for unauthenticated WhatsApp placeholder sessions
        # (e.g. WA_SESSION_1, WA_SESSION_2). Prevents duplicate real identities while
        # allowing multiple empty placeholder rows.
        Index(
            "uq_sender_channel_identity",
            "channel",
            "identity",
            unique=True,
            sqlite_where=text("identity != ''"),
        ),
    )

    def to_domain(self) -> SenderAccount:
        return SenderAccount(
            id=self.id,
            channel=Channel(self.channel),
            provider=self.provider,
            identity=self.identity,
            display_name=self.display_name,
            status=_coerce_enum(SenderStatus, self.status, "sender_account.status"),
            credential_ref=self.credential_ref,
            session_ref=self.session_ref,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            daily_limit=self.daily_limit,
            hourly_limit=self.hourly_limit,
        )

    @classmethod
    def from_domain(cls, entity: SenderAccount) -> SenderAccountModel:
        return cls(
            id=entity.id,
            channel=entity.channel.value if isinstance(entity.channel, Channel) else str(entity.channel),
            provider=entity.provider,
            identity=entity.identity,
            display_name=entity.display_name,
            status=entity.status.value if isinstance(entity.status, SenderStatus) else str(entity.status),
            credential_ref=entity.credential_ref,
            session_ref=entity.session_ref,
            created_at=entity.created_at,
            last_used_at=entity.last_used_at,
            daily_limit=entity.daily_limit,
            hourly_limit=entity.hourly_limit,
        )


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="IDLE", nullable=False)
    template_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    sender_account_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    # Relationships
    # Keep attempts on campaign delete (DB FK is ON DELETE SET NULL). Never delete-orphan audit rows.
    outreach_attempts: Mapped[List[OutreachAttemptModel]] = relationship(
        "OutreachAttemptModel", back_populates="campaign"
    )

    def to_domain(self) -> Campaign:
        try:
            t_ids = json.loads(self.template_ids_json)
        except Exception:
            t_ids = []
        try:
            s_ids = json.loads(self.sender_account_ids_json)
        except Exception:
            s_ids = []
        try:
            meta = json.loads(self.metadata_json)
        except Exception:
            meta = {}

        return Campaign(
            id=self.id,
            name=self.name,
            channel=Channel(self.channel),
            status=_coerce_enum(CampaignStatus, self.status, "campaign.status"),
            template_ids=t_ids,
            sender_account_ids=s_ids,
            created_at=self.created_at,
            started_at=self.started_at,
            ended_at=self.ended_at,
            metadata=meta,
        )

    @classmethod
    def from_domain(cls, entity: Campaign) -> CampaignModel:
        return cls(
            id=entity.id,
            name=entity.name,
            channel=entity.channel.value if isinstance(entity.channel, Channel) else str(entity.channel),
            status=entity.status.value if isinstance(entity.status, CampaignStatus) else str(entity.status),
            template_ids_json=json.dumps(entity.template_ids or []),
            sender_account_ids_json=json.dumps(entity.sender_account_ids or []),
            created_at=entity.created_at,
            started_at=entity.started_at,
            ended_at=entity.ended_at,
            metadata_json=json.dumps(entity.metadata or {}),
        )


class MessageTemplateModel(Base):
    __tablename__ = "message_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attachment_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_domain(self) -> MessageTemplate:
        return MessageTemplate(
            id=self.id,
            name=self.name,
            channel=Channel(self.channel),
            body=self.body,
            subject=self.subject,
            attachment_ref=self.attachment_ref,
            phone_number=getattr(self, "phone_number", None),
            active=getattr(self, "active", True),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_domain(cls, entity: MessageTemplate) -> MessageTemplateModel:
        return cls(
            id=entity.id,
            name=entity.name,
            channel=entity.channel.value if isinstance(entity.channel, Channel) else str(entity.channel),
            body=entity.body,
            subject=entity.subject,
            attachment_ref=entity.attachment_ref,
            phone_number=entity.phone_number,
            active=entity.active,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class OutreachAttemptModel(Base):
    __tablename__ = "outreach_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("contacts.contact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_account_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("sender_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    message_body_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("message_templates.id", ondelete="SET NULL"), nullable=True
    )
    subject_snapshot: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attachment_snapshot: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    failure_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recovery_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    contact: Mapped[ContactModel] = relationship("ContactModel", back_populates="outreach_attempts")
    campaign: Mapped[Optional[CampaignModel]] = relationship("CampaignModel", back_populates="outreach_attempts")

    def to_domain(self) -> OutreachAttempt:
        return OutreachAttempt(
            id=self.id,
            contact_id=self.contact_id,
            sender_account_id=self.sender_account_id,
            channel=Channel(self.channel),
            attempt_type=_coerce_enum(AttemptType, self.attempt_type, "outreach_attempt.attempt_type"),
            status=_coerce_enum(OutreachStatus, self.status, "outreach_attempt.status"),
            idempotency_key=self.idempotency_key,
            message_body_snapshot=self.message_body_snapshot,
            destination=self.destination,
            campaign_id=self.campaign_id,
            template_id=self.template_id,
            subject_snapshot=self.subject_snapshot,
            attachment_snapshot=self.attachment_snapshot,
            prepared_at=self.prepared_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            failure_code=self.failure_code,
            failure_detail=self.failure_detail,
            provider_reference=self.provider_reference,
            recovery_notes=self.recovery_notes,
        )

    @classmethod
    def from_domain(cls, entity: OutreachAttempt) -> OutreachAttemptModel:
        return cls(
            id=entity.id,
            contact_id=entity.contact_id,
            sender_account_id=entity.sender_account_id,
            channel=entity.channel.value if isinstance(entity.channel, Channel) else str(entity.channel),
            attempt_type=entity.attempt_type.value
            if isinstance(entity.attempt_type, AttemptType)
            else str(entity.attempt_type),
            status=entity.status.value if isinstance(entity.status, OutreachStatus) else str(entity.status),
            idempotency_key=entity.idempotency_key,
            message_body_snapshot=entity.message_body_snapshot,
            destination=entity.destination,
            campaign_id=entity.campaign_id,
            template_id=entity.template_id,
            subject_snapshot=entity.subject_snapshot,
            attachment_snapshot=entity.attachment_snapshot,
            prepared_at=entity.prepared_at,
            started_at=entity.started_at,
            completed_at=entity.completed_at,
            failure_code=entity.failure_code,
            failure_detail=entity.failure_detail,
            provider_reference=entity.provider_reference,
            recovery_notes=entity.recovery_notes,
        )


class FollowUpReminderModel(Base):
    __tablename__ = "follow_up_reminders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("contacts.contact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    contact: Mapped[ContactModel] = relationship("ContactModel", back_populates="reminders")

    def to_domain(self) -> FollowUpReminder:
        return FollowUpReminder(
            id=self.id,
            contact_id=self.contact_id,
            due_at=self.due_at,
            reason=self.reason,
            status=_coerce_enum(ReminderStatus, self.status, "reminder.status"),
            created_at=self.created_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_domain(cls, entity: FollowUpReminder) -> FollowUpReminderModel:
        return cls(
            id=entity.id,
            contact_id=entity.contact_id,
            due_at=entity.due_at,
            reason=entity.reason,
            status=entity.status.value if isinstance(entity.status, ReminderStatus) else str(entity.status),
            created_at=entity.created_at,
            completed_at=entity.completed_at,
        )


class CRMEventModel(Base):
    __tablename__ = "crm_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("contacts.contact_id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_state_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SuppressionRecordModel(Base):
    __tablename__ = "suppression_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suppression_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "PHONE", "EMAIL", "CANONICAL_KEY", "SOURCE_ROW"
    identifier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), default="MANUAL_CRM_DELETION", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (UniqueConstraint("suppression_type", "identifier", name="uq_suppression_type_identifier"),)
