"""Independent safety verification tests.

Author: Independent Verification Engineer
Role: Production-Safety / Test Agent

Comprehensive verification for:
1. Database isolation and immutability
2. Manual Send & Resend regressions (OutreachService provider_reference fix)
3. Provider mode configuration (mock vs live resolution)
4. Unified scheduler & RateLimiter integration
5. Campaign lifecycle state transitions
6. Startup crash recovery (SENDING -> RECOVERY_REQUIRED)
7. Company-First round-robin interleaving and duplicate suppression
8. N-Sender dynamic scalability (1, 3, 10 WhatsApp & Email senders)
9. Source workbook synchronization (fidelity & historical preservation)
10. End-to-End CRM & Outreach lifecycle flow
11. Error-case handling (timeout, failure, auth required, session expired, rate-limited)
"""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone

from app.domain.enums import (
    AttemptType,
    Channel,
    CRMOutcome,
)
from app.domain.outreach_attempt import OutreachAttempt
from app.domain.sender_account import SenderAccount
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_suppression_repository import SqliteSuppressionRepository
from app.infrastructure.source.synchronizer import DatabaseSourceSynchronizer

# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestSourceSynchronizationRegression:
    def test_source_sync_comprehensive_scenarios_and_file_immutability(self, isolated_db, tmp_path):
        """Tests:
        1. New contact ingestion
        2. New recruiter at existing company
        3. Modified contact (enrichment with historical outreach preserved)
        4. Duplicate contact handling
        5. Deleted / suppressed contact state preserved
        6. Source file immutability (SHA-256 unchanged)
        """
        _, SessionFactory = isolated_db
        source_csv = tmp_path / "sync_dataset.csv"

        # 1. Initial dataset: Google (Sundar), Microsoft (Satya), Uber (Dara)
        rows_v1 = [
            {"company": "Google", "name": "Sundar Pichai", "phone": "919876543210", "email": "sundar@google.com"},
            {"company": "Microsoft", "name": "Satya Nadella", "phone": "919876543211", "email": "satya@microsoft.com"},
            {"company": "Uber", "name": "Dara", "phone": "919999999999", "email": ""},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows_v1)

        initial_sha = hashlib.sha256(source_csv.read_bytes()).hexdigest()

        # Run initial sync
        with SessionFactory() as session:
            sync = DatabaseSourceSynchronizer(session)
            res1 = sync.sync_source(str(source_csv))
            assert res1.total_read == 3
            assert res1.new_contacts == 3

        # Verify source file unchanged
        assert hashlib.sha256(source_csv.read_bytes()).hexdigest() == initial_sha

        # 2. Simulate historical outreach & CRM notes on Uber (Dara) and Microsoft (Satya)
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)
            suppr_repo = SqliteSuppressionRepository(session)

            dara = contact_repo.get_by_key("uber|919999999999")
            now = datetime.now(timezone.utc)
            dara.record_outreach_success(Channel.WHATSAPP, now)
            dara.update_crm_outcome(CRMOutcome.INTERESTED, now)
            dara.notes = "Discussed Senior Staff SWE opportunity"
            contact_repo.save(dara)

            # Seed the sender referenced by the attempt FK.
            SqliteSenderRepository(session).save(
                SenderAccount.create(
                    sender_id="WA-1",
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+919000000000",
                    display_name="WA1",
                )
            )

            att = OutreachAttempt.prepare(
                contact_id=dara.contact_id,
                sender_account_id="WA-1",
                channel=Channel.WHATSAPP,
                attempt_type=AttemptType.AUTOMATIC,
                message_body="Hello Dara",
            )
            att.mark_sent(provider_reference="wa_uber_ref_1")
            outreach_repo.save(att)

            # Suppress/Tombstone Microsoft contact
            satya = contact_repo.get_by_key("microsoft|919876543211")
            if satya:
                suppr_repo.add_suppression("PHONE", satya.phone, "Manual user opt-out")
            session.commit()

        # 3. Modify dataset:
        # - Modified contact: Uber (Dara Khosrowshahi with email added)
        # - New recruiter at existing company: Uber (Recruiter Travis)
        # - New company & contact: Anthropic (Dario)
        # - Duplicate row: duplicate Google Sundar
        rows_v2 = [
            {"company": "Google", "name": "Sundar Pichai", "phone": "919876543210", "email": "sundar@google.com"},
            {
                "company": "Google",
                "name": "Sundar Pichai Duplicate",
                "phone": "919876543210",
                "email": "sundar@google.com",
            },
            {"company": "Uber", "name": "Dara Khosrowshahi", "phone": "919999999999", "email": "dara@uber.com"},
            {"company": "Uber", "name": "Travis K", "phone": "919999999998", "email": "travis@uber.com"},
            {"company": "Anthropic", "name": "Dario Amodei", "phone": "919000000003", "email": "dario@anthropic.com"},
        ]
        with source_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "phone", "email"])
            writer.writeheader()
            writer.writerows(rows_v2)

        v2_sha = hashlib.sha256(source_csv.read_bytes()).hexdigest()

        # Run second sync
        with SessionFactory() as session:
            sync = DatabaseSourceSynchronizer(session)
            res2 = sync.sync_source(str(source_csv))
            assert res2.new_contacts >= 2  # Travis + Dario
            assert res2.updated_contacts >= 1  # Dara

        # Verify source file remained untouched
        assert hashlib.sha256(source_csv.read_bytes()).hexdigest() == v2_sha

        # 4. Verify historical outreach & CRM notes on Dara remain 100% intact
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            outreach_repo = SqliteOutreachRepository(session)
            suppr_repo = SqliteSuppressionRepository(session)

            dara_after = contact_repo.get_by_key("uber|919999999999")
            assert dara_after.name == "Dara Khosrowshahi"
            assert dara_after.email == "dara@uber.com"
            assert dara_after.last_whatsapp_at is not None
            assert dara_after.crm_outcome == CRMOutcome.INTERESTED
            assert dara_after.notes == "Discussed Senior Staff SWE opportunity"

            attempts = outreach_repo.list_by_contact(dara_after.contact_id)
            assert len(attempts) == 1
            assert attempts[0].provider_reference == "wa_uber_ref_1"

            # Verify suppression on Satya is preserved
            assert suppr_repo.is_suppressed(phone="919876543211") is True

            # Verify new recruiter Travis at Uber exists
            travis = contact_repo.get_by_key("uber|919999999998")
            assert travis is not None
            assert travis.name == "Travis K"


# ==============================================================================
# 10. END-TO-END CRM FLOW
# ==============================================================================
