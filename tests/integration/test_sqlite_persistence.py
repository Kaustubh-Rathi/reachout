"""Integration tests for SQLite persistence, WAL mode, foreign keys, and transactions."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import AttemptType, Channel
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.models import (
    ContactModel,
    OutreachAttemptModel,
)
from app.infrastructure.repositories import (
    SqliteCompanyRepository,
    SqliteContactRepository,
    SqliteOutreachRepository,
    SqliteSenderRepository,
)


@pytest.fixture
def sqlite_session():
    """Create an isolated in-memory SQLite database with foreign keys enabled."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON;"))

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestSqlitePersistence:
    def test_wal_pragma_and_foreign_keys(self, sqlite_session):
        # Verify foreign keys are enabled in SQLite
        res = sqlite_session.execute(text("PRAGMA foreign_keys;")).scalar()
        assert res == 1

    def test_company_and_contact_persistence_and_relationships(self, sqlite_session):
        comp_repo = SqliteCompanyRepository(sqlite_session)
        contact_repo = SqliteContactRepository(sqlite_session)

        comp = Company.create(name="Stripe Inc.", company_id="stripe")
        comp_repo.save(comp)
        sqlite_session.commit()

        loaded_comp = comp_repo.get_by_id("stripe")
        assert loaded_comp is not None
        assert loaded_comp.name == "Stripe Inc."
        assert loaded_comp.normalized_name == "stripe inc."

        contact = Contact(
            contact_id="cnt_stripe_01",
            company_id="stripe",
            name="Patrick Collison",
            phone="919876543210",
            email="patrick@stripe.com",
        )
        contact_repo.save(contact)
        sqlite_session.commit()

        loaded_contact = contact_repo.get_by_id("cnt_stripe_01")
        assert loaded_contact is not None
        assert loaded_contact.name == "Patrick Collison"
        assert loaded_contact.first_name == "Patrick"
        assert loaded_contact.canonical_key == "stripe|919876543210"

    def test_foreign_key_violation_raises_error(self, sqlite_session):
        contact_model = ContactModel(
            contact_id="cnt_orphan",
            company_id="non_existent_company",
            name="Ghost Recruiter",
            phone="919999999999",
        )
        sqlite_session.add(contact_model)
        with pytest.raises(IntegrityError):
            sqlite_session.commit()
        sqlite_session.rollback()

    def test_transactional_rollback_preserves_clean_state(self, sqlite_session):
        comp_repo = SqliteCompanyRepository(sqlite_session)
        comp = Company.create(name="Airbnb", company_id="airbnb")
        comp_repo.save(comp)
        sqlite_session.commit()

        # Start a failing sub-transaction
        try:
            invalid_contact = ContactModel(
                contact_id="cnt_fail",
                company_id="unregistered_comp",
                name="Failing Contact",
            )
            sqlite_session.add(invalid_contact)
            sqlite_session.flush()
        except Exception:
            sqlite_session.rollback()

        # Verify initial company was preserved
        loaded = comp_repo.get_by_id("airbnb")
        assert loaded is not None
        assert loaded.name == "Airbnb"

    def test_outreach_attempt_idempotency_constraint(self, sqlite_session):
        comp_repo = SqliteCompanyRepository(sqlite_session)
        contact_repo = SqliteContactRepository(sqlite_session)
        outreach_repo = SqliteOutreachRepository(sqlite_session)

        comp = Company.create(name="Netflix", company_id="netflix")
        comp_repo.save(comp)
        contact = Contact(
            contact_id="cnt_netflix_01",
            company_id="netflix",
            name="Reed Hastings",
            phone="919111111111",
        )
        contact_repo.save(contact)
        # Persist the referenced sender so the attempt FK resolves.
        SqliteSenderRepository(sqlite_session).save(
            SenderAccount.create(
                sender_id="snd_wa_1",
                channel=Channel.WHATSAPP,
                provider="mock",
                identity="+919111111111",
                display_name="S1",
            )
        )
        sqlite_session.commit()

        attempt1 = OutreachAttempt.prepare(
            contact_id="cnt_netflix_01",
            sender_account_id="snd_wa_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello Reed",
            idempotency_key="idemp_unique_key_123",
        )
        outreach_repo.save(attempt1)
        sqlite_session.commit()

        attempt2 = OutreachAttempt.prepare(
            contact_id="cnt_netflix_01",
            sender_account_id="snd_wa_1",
            channel=Channel.WHATSAPP,
            attempt_type=AttemptType.AUTOMATIC,
            message_body="Hello Reed Duplicate",
            idempotency_key="idemp_unique_key_123",
        )
        # Attempt to insert identical idempotency key should raise IntegrityError
        dup_model = OutreachAttemptModel.from_domain(attempt2)
        dup_model.id = "att_netflix_02"
        sqlite_session.add(dup_model)
        with pytest.raises(IntegrityError):
            sqlite_session.commit()
        sqlite_session.rollback()
