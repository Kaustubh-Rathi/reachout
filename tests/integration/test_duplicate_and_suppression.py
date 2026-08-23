"""Integration tests for deletion tombstones, suppression records, and duplicate prevention."""

import csv
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.policies.duplicate_policy import evaluate_automatic_eligibility
from app.domain.policies.resend_policy import prepare_manual_resend
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.models import ContactModel
from app.infrastructure.repositories import (
    SqliteCompanyRepository,
    SqliteContactRepository,
    SqliteOutreachRepository,
    SqliteSenderRepository,
    SqliteSuppressionRepository,
)
from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer


@pytest.fixture
def tombstone_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tombstone_test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestDuplicateAndSuppression:
    def test_crm_deletion_tombstone_prevents_resurrection_on_sync(self, tombstone_session, tmp_path):
        source_csv = tmp_path / "contacts_to_delete.csv"
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerow({"company": "Salesforce", "name": "Marc Benioff", "phone": "919876500001", "email": "marc@salesforce.com"})

        synchronizer = DatabaseSourceSynchronizer(tombstone_session)
        summary1 = synchronizer.sync_source(str(source_csv))
        assert summary1.new_contacts == 1

        contact_repo = SqliteContactRepository(tombstone_session)
        suppression_repo = SqliteSuppressionRepository(tombstone_session)

        marc = contact_repo.get_by_key("salesforce|919876500001")
        assert marc is not None

        # Operator deletes Marc from CRM -> Add tombstone suppression record and remove active DB record
        suppression_repo.add_suppression(
            suppression_type="CANONICAL_KEY",
            identifier=marc.canonical_key,
            reason="Operator requested lead deletion",
        )
        # Simulate active removal from CRM
        db_marc = tombstone_session.get(ContactModel, marc.contact_id)
        if db_marc:
            tombstone_session.delete(db_marc)
            tombstone_session.commit()

        assert contact_repo.get_by_key("salesforce|919876500001") is None

        # Re-run synchronization against original source file (which still contains Marc)
        summary2 = synchronizer.sync_source(str(source_csv))
        assert summary2.new_contacts == 0
        assert contact_repo.get_by_key("salesforce|919876500001") is None, "Suppressed contact was incorrectly resurrected!"

    def test_automatic_duplicate_protection_blocks_resending_successful_outreach(self, tombstone_session):
        comp_repo = SqliteCompanyRepository(tombstone_session)
        contact_repo = SqliteContactRepository(tombstone_session)
        outreach_repo = SqliteOutreachRepository(tombstone_session)

        comp = Company.create(name="Adobe", company_id="adobe")
        comp_repo.save(comp)
        contact = Contact(
            contact_id="cnt_adobe_01",
            company_id="adobe",
            name="Shantanu Narayen",
            phone="919888888888",
        )
        contact_repo.save(contact)

        # 1. Initially eligible
        elig1 = evaluate_automatic_eligibility(contact, Channel.WHATSAPP)
        assert elig1.is_eligible is True

        # 2. Record successful attempt
        now = datetime.now(timezone.utc)
        contact.record_outreach_success(Channel.WHATSAPP, now)
        contact_repo.save(contact)

        sender = SenderAccount.create(
            sender_id="snd_wa_1",
            channel=Channel.WHATSAPP,
            provider="mock",
            identity="+919999999999",
            display_name="Line 1",
        )
        # Persist the sender so the attempt's sender_account_id FK resolves.
        SqliteSenderRepository(tombstone_session).save(sender)

        attempt = OutreachAttempt.prepare(
            contact_id=contact.contact_id,
            sender_account_id=sender.id,
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello",
        )
        attempt.mark_sent("ref_123")
        outreach_repo.save(attempt)
        tombstone_session.commit()

        # 3. Automatic outreach must now be blocked
        hist = outreach_repo.list_by_contact(contact.contact_id)
        elig2 = evaluate_automatic_eligibility(contact, Channel.WHATSAPP, hist)
        assert elig2.is_eligible is False
        assert "ALREADY_SENT" in elig2.reason

        # 4. Manual resend is explicitly permitted via policy
        manual_attempt = prepare_manual_resend(
            contact=contact,
            sender_account=sender,
            channel=Channel.WHATSAPP,
            rendered_body="Hello again",
        )
        assert manual_attempt.attempt_type == AttemptType.RESEND
        assert manual_attempt.idempotency_key != attempt.idempotency_key
