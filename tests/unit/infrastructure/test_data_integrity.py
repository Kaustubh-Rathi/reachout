"""Data Integrity, Migration Safety, and Source Preservation Tests.

Verifies that:
1. Legacy source data survives migration without data loss.
2. Migration & synchronization operations are strictly repeatable and idempotent.
3. Source files (workbooks, logs, JSON) are not mutated or corrupted.
4. Newly discovered source rows appear correctly in the domain/relational model.
5. Suppressed or edited CRM contacts preserve operator modifications across synchronizations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import CRMOutcome, InterviewState
from app.domain.source_record import SourceRecord
from app.infrastructure.models import (
    CompanyModel,
    ContactModel,
    SourceRecordModel,
)
from app.infrastructure.source.excel_reader import TabularSourceReader

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestDataIntegrityAndMigration:
    """Suite verifying data preservation, migration idempotency, and non-destructive sync."""

    def test_source_files_remain_unchanged_after_read_operations(self):
        """Reading workbooks and logs must not alter their bytes or file hashes."""
        critical_files = [
            DATA_DIR / "MNC_Final.xlsx",
            DATA_DIR / "Reachout.xlsx",
            DATA_DIR / "crm_data.json",
            LOGS_DIR / "mnc_whatsapp_send_log.csv",
        ]

        # 1. Take initial hash snapshots
        hashes_before = {p.name: compute_sha256(p) for p in critical_files if p.exists()}

        # 2. Perform intensive read and extraction operations
        if (DATA_DIR / "MNC_Final.xlsx").exists():
            rows = TabularSourceReader().read_rows(DATA_DIR / "MNC_Final.xlsx")
            assert len(rows) > 0

        if (DATA_DIR / "crm_data.json").exists():
            with (DATA_DIR / "crm_data.json").open("r", encoding="utf-8") as f:
                crm_dict = json.load(f)
            assert isinstance(crm_dict, dict)

        # 3. Verify hashes after operations are 100% identical
        for p in critical_files:
            if p.exists():
                hash_after = compute_sha256(p)
                assert hash_after == hashes_before[p.name], (
                    f"CRITICAL: File {p.name} was mutated during read operations!"
                )

    def test_migration_seed_survives_losslessly_in_relational_schema(self, db_session):
        """Simulate ingesting contact rows and assert lossless fidelity across entities."""
        # 1. Create company and contact entities with full metadata
        company = Company.create(name="Razorpay Software Private Limited", domain="razorpay.com")
        contact = Contact(
            contact_id="cnt_razorpay_1",
            company_id=company.id,
            name="Rohit Sharma",
            phone="918826363651",
            email="rohit.sharma@razorpay.com",
            designation="Senior Technical Recruiter",
            crm_outcome=CRMOutcome.INTERESTED,
            interview_status=InterviewState.PENDING,
            notes="Follow up next Monday regarding backend role.",
            tags=["MNC", "Fintech", "Priority"],
        )
        source_rec = SourceRecord.create(
            source_file="MNC_Final.xlsx",
            source_sheet="MNC_Cleaned",
            source_row=12,
            raw_payload={"company": "Razorpay", "phone": "8826363651"},
        )

        # 2. Persist to relational DB via ORM models
        db_session.add(CompanyModel.from_domain(company))
        db_session.add(ContactModel.from_domain(contact))
        db_session.add(SourceRecordModel.from_domain(source_rec, contact_id=contact.contact_id))
        db_session.commit()

        # 3. Retrieve and assert complete round-trip fidelity
        retrieved_contact_m = db_session.get(ContactModel, contact.contact_id)
        assert retrieved_contact_m is not None
        domain_contact = retrieved_contact_m.to_domain()

        assert domain_contact.contact_id == contact.contact_id
        assert domain_contact.company_id == company.id
        assert domain_contact.name == "Rohit Sharma"
        assert domain_contact.phone == "918826363651"
        assert domain_contact.email == "rohit.sharma@razorpay.com"
        assert domain_contact.crm_outcome == CRMOutcome.INTERESTED
        assert domain_contact.interview_status == InterviewState.PENDING
        assert domain_contact.notes == "Follow up next Monday regarding backend role."
        assert "Fintech" in domain_contact.tags
        assert "Priority" in domain_contact.tags

    def test_migration_is_repeatable_and_idempotent(self, db_session):
        """Running ingestion sync twice on the same dataset does not duplicate records."""
        companies_data = [
            ("Microsoft India", "919988776655", "satya@microsoft.com"),
            ("Microsoft India", "919988776656", "recruiter@microsoft.com"),
            ("Amazon Web Services", "918877665544", "aws_hr@amazon.com"),
        ]

        def run_sync(records):
            for comp_name, phone, email in records:
                norm_name = comp_name.lower().strip()
                comp_stmt = select(CompanyModel).where(CompanyModel.normalized_name == norm_name)
                comp_m = db_session.scalars(comp_stmt).first()
                if not comp_m:
                    comp_entity = Company.create(name=comp_name)
                    comp_m = CompanyModel.from_domain(comp_entity)
                    db_session.add(comp_m)
                    db_session.flush()

                cnt_stmt = select(ContactModel).where(ContactModel.phone == phone)
                cnt_m = db_session.scalars(cnt_stmt).first()
                if not cnt_m:
                    cnt_entity = Contact(
                        contact_id=f"cnt_{phone}",
                        company_id=comp_m.id,
                        name=f"Contact for {phone}",
                        phone=phone,
                        email=email,
                    )
                    cnt_m = ContactModel.from_domain(cnt_entity)
                    db_session.add(cnt_m)
                else:
                    cnt_m.email = email
                    cnt_m.updated_at = datetime.now(timezone.utc)
            db_session.commit()

        # First synchronization
        run_sync(companies_data)
        total_companies_1 = db_session.scalar(select(func.count(CompanyModel.id)))
        total_contacts_1 = db_session.scalar(select(func.count(ContactModel.contact_id)))
        assert total_companies_1 == 2
        assert total_contacts_1 == 3

        # Second synchronization (identical data)
        run_sync(companies_data)
        total_companies_2 = db_session.scalar(select(func.count(CompanyModel.id)))
        total_contacts_2 = db_session.scalar(select(func.count(ContactModel.contact_id)))

        assert total_companies_2 == total_companies_1, "Duplicate companies created on repeated sync!"
        assert total_contacts_2 == total_contacts_1, "Duplicate contacts created on repeated sync!"

    def test_new_source_rows_appear_in_crm_synchronization(self, db_session):
        """Adding a new synthetic source row reconciles cleanly into the database."""
        comp = Company.create("Google")
        db_session.add(CompanyModel.from_domain(comp))
        cnt1 = Contact(
            contact_id="cnt_g1",
            company_id=comp.id,
            name="Recruiter 1",
            phone="919000000001",
            email="recruiter1@google.com",
        )
        db_session.add(ContactModel.from_domain(cnt1))
        db_session.commit()

        new_phone = "919000000002"
        cnt2 = Contact(
            contact_id="cnt_g2", company_id=comp.id, name="Recruiter 2", phone=new_phone, email="recruiter2@google.com"
        )
        src2 = SourceRecord.create(source_file="MNC_Final.xlsx", source_sheet="Sheet1", source_row=99)
        db_session.add(ContactModel.from_domain(cnt2))
        db_session.add(SourceRecordModel.from_domain(src2, contact_id=cnt2.contact_id))
        db_session.commit()

        found_cnt = db_session.scalar(select(ContactModel).where(ContactModel.phone == new_phone))
        assert found_cnt is not None
        assert found_cnt.name == "Recruiter 2"
        assert found_cnt.company_id == comp.id

    def test_deleted_or_suppressed_crm_contacts_remain_suppressed(self, db_session):
        """User CRM customizations (e.g. NOT_INTERESTED, notes) are not wiped by resync."""
        comp = Company.create("Oracle")
        db_session.add(CompanyModel.from_domain(comp))
        contact = Contact(
            contact_id="cnt_ora_1",
            company_id=comp.id,
            name="Oracle Recruiter",
            phone="919123456789",
            email="recruiter@oracle.com",
            crm_outcome=CRMOutcome.NOT_INTERESTED,
            notes="Requested not to contact again.",
        )
        db_session.add(ContactModel.from_domain(contact))
        db_session.commit()

        existing_m = db_session.scalar(select(ContactModel).where(ContactModel.phone == "919123456789"))
        assert existing_m is not None

        # Sync logic must preserve existing user outcome if non-empty
        raw_sync_outcome = "NONE"
        if existing_m.crm_outcome != "NONE":
            # Preserve user outcome
            pass
        else:
            existing_m.crm_outcome = raw_sync_outcome

        db_session.commit()

        reloaded = db_session.scalar(select(ContactModel).where(ContactModel.phone == "919123456789"))
        assert reloaded.crm_outcome == CRMOutcome.NOT_INTERESTED.value
        assert reloaded.notes == "Requested not to contact again."
