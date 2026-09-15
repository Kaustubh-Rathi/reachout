"""Integration tests for one-way source synchronization and reconciliation."""

import csv
import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.enums import Channel, CRMOutcome
from app.infrastructure.database import Base
from app.infrastructure.repositories import (
    SqliteCompanyRepository,
    SqliteContactRepository,
)
from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer


def compute_file_sha256(path) -> str:
    """Compute a byte-for-byte SHA-256 of a file to prove immutability."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture
def sync_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sync_test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestSourceSynchronization:
    def test_sync_creates_companies_and_contacts_without_mutating_source(self, sync_session, tmp_path):
        source_csv = tmp_path / "test_contacts.csv"
        rows = [
            {"company": "Google", "name": "Sundar Pichai", "phone": "919876543210", "email": "sundar@google.com"},
            {"company": "Microsoft", "name": "Satya Nadella", "phone": "919876543211", "email": "satya@microsoft.com"},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows)

        initial_sha256 = compute_file_sha256(source_csv)

        synchronizer = DatabaseSourceSynchronizer(sync_session)
        summary = synchronizer.sync_source(str(source_csv))

        assert summary.total_read == 2
        assert summary.new_contacts == 2
        assert summary.errors == []

        # Verify source file was untouched
        post_sha256 = compute_file_sha256(source_csv)
        assert initial_sha256 == post_sha256, "Source file was mutated during synchronization!"

        # Verify DB records
        contact_repo = SqliteContactRepository(sync_session)
        comp_repo = SqliteCompanyRepository(sync_session)

        google_comp = comp_repo.get_by_normalized_name("google")
        assert google_comp is not None

        sundar = contact_repo.get_by_key("google|919876543210")
        assert sundar is not None
        assert sundar.name == "Sundar Pichai"
        assert sundar.email == "sundar@google.com"

    def test_sync_preserves_historical_outreach_data_when_contact_updated(self, sync_session, tmp_path):
        source_csv = tmp_path / "test_contacts_v1.csv"
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerow({"company": "Uber", "name": "Dara", "phone": "919999999999", "email": ""})

        synchronizer = DatabaseSourceSynchronizer(sync_session)
        synchronizer.sync_source(str(source_csv))

        contact_repo = SqliteContactRepository(sync_session)
        dara = contact_repo.get_by_key("uber|919999999999")
        assert dara is not None

        # Simulate historical outreach and CRM update
        now = datetime.now(timezone.utc)
        dara.record_outreach_success(Channel.WHATSAPP, now)
        dara.update_crm_outcome(CRMOutcome.INTERESTED, now)
        dara.notes = "Discussed referral on call"
        dara.tags = ["referral", "ride-sharing"]
        contact_repo.save(dara)
        sync_session.commit()

        # Update source CSV with newly enriched email & full name
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerow(
                {"company": "Uber", "name": "Dara Khosrowshahi", "phone": "919999999999", "email": "dara@uber.com"}
            )

        summary2 = synchronizer.sync_source(str(source_csv))
        assert summary2.updated_contacts == 1
        assert summary2.new_contacts == 0

        # Reload contact and verify metadata was preserved
        dara_reloaded = contact_repo.get_by_key("uber|919999999999")
        assert dara_reloaded.name == "Dara Khosrowshahi"
        assert dara_reloaded.email == "dara@uber.com"
        assert dara_reloaded.last_whatsapp_at is not None
        assert dara_reloaded.crm_outcome == CRMOutcome.INTERESTED
        assert dara_reloaded.notes == "Discussed referral on call"
        assert "referral" in dara_reloaded.tags

    def test_sync_supports_multiple_recruiters_at_same_company(self, sync_session, tmp_path):
        source_csv = tmp_path / "multi_recruiters.csv"
        rows = [
            {"company": "Amazon", "name": "Recruiter Alice", "phone": "919000000001", "email": "alice@amazon.com"},
            {"company": "Amazon", "name": "Recruiter Bob", "phone": "919000000002", "email": "bob@amazon.com"},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows)

        synchronizer = DatabaseSourceSynchronizer(sync_session)
        summary = synchronizer.sync_source(str(source_csv))
        assert summary.new_contacts == 2

        contact_repo = SqliteContactRepository(sync_session)
        amazon_contacts = contact_repo.find_by_company("amazon")
        assert len(amazon_contacts) == 2
        names = {c.name for c in amazon_contacts}
        assert names == {"Recruiter Alice", "Recruiter Bob"}
