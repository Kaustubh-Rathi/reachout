"""SQLite / SQLAlchemy implementation of ContactRepository port."""

from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.domain.contact import Contact
from app.infrastructure.models import ContactModel, SourceRecordModel
from app.ports.repositories import ContactRepository


class SqliteContactRepository(ContactRepository):
    """Repository handling persistence and queries for Contact entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, contact_id: str) -> Optional[Contact]:
        stmt = (
            select(ContactModel)
            .options(joinedload(ContactModel.source_records))
            .where(ContactModel.contact_id == contact_id)
        )
        model = self.session.scalars(stmt).first()
        return model.to_domain() if model else None

    def get_by_key(self, canonical_key: str) -> Optional[Contact]:
        """Lookup by composite canonical key (company_id|phone or company_id|email)."""
        if "|" not in canonical_key:
            return None
        parts = canonical_key.split("|", 1)
        company_id, target = parts[0].strip().casefold(), parts[1].strip()

        # Try phone match first, then email match
        stmt = (
            select(ContactModel)
            .options(joinedload(ContactModel.source_records))
            .where(
                ContactModel.company_id == company_id,
                (ContactModel.phone == target) | (ContactModel.email == target.lower()),
            )
            .limit(1)
        )
        model = self.session.scalars(stmt).first()
        return model.to_domain() if model else None

    def list_all(self) -> List[Contact]:
        stmt = (
            select(ContactModel)
            .options(joinedload(ContactModel.source_records))
            .order_by(ContactModel.company_id, ContactModel.name)
        )
        models = self.session.scalars(stmt).unique().all()
        return [m.to_domain() for m in models]

    def find_by_company(self, company_id: str) -> List[Contact]:
        stmt = (
            select(ContactModel)
            .options(joinedload(ContactModel.source_records))
            .where(ContactModel.company_id == company_id)
            .order_by(ContactModel.name)
        )
        models = self.session.scalars(stmt).unique().all()
        return [m.to_domain() for m in models]

    def save(self, contact: Contact) -> Contact:
        existing = self.session.get(ContactModel, contact.contact_id)
        if existing:
            existing.company_id = contact.company_id
            existing.name = contact.name
            existing.designation = contact.designation or ""
            existing.phone = contact.phone
            existing.email = contact.email
            existing.updated_at = contact.updated_at
            existing.last_whatsapp_at = contact.last_whatsapp_at
            existing.last_email_at = contact.last_email_at
            existing.last_activity_at = contact.last_activity_at
            existing.crm_outcome = contact.crm_outcome.value
            existing.interview_status = contact.interview_status.value
            existing.interested_at = contact.interested_at
            existing.interview_status_changed_at = contact.interview_status_changed_at
            existing.notes = contact.notes or ""
            existing.tags_json = json.dumps(contact.tags or [])
        else:
            model = ContactModel.from_domain(contact)
            self.session.add(model)

        # Upsert source reference if present
        if contact.source_reference:
            src = contact.source_reference
            # Check if source record already exists for this contact and fingerprint
            stmt = select(SourceRecordModel).where(
                SourceRecordModel.contact_id == contact.contact_id,
                SourceRecordModel.source_fingerprint == src.source_fingerprint,
            )
            existing_src = self.session.scalars(stmt).first()
            if existing_src:
                existing_src.last_seen_at = src.last_seen_at
            else:
                new_src = SourceRecordModel.from_domain(src, contact_id=contact.contact_id)
                self.session.add(new_src)

        self.session.flush()
        return contact

    def delete(self, contact_id: str) -> bool:
        model = self.session.get(ContactModel, contact_id)
        if not model:
            return False
        self.session.delete(model)
        self.session.flush()
        return True
