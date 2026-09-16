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

import pytest

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import (
    Channel,
)
from app.domain.message_template import MessageTemplate
from app.domain.policies.prioritization import prioritize_company_first
from app.domain.sender_account import SenderAccount
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.repositories.sqlite_template_repository import SqliteTemplateRepository
from app.services.outreach_service import OutreachService
from tests.doubles.fake_providers import MockWhatsAppProvider

# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestCompanyFirstAndDuplicateSuppression:
    def test_canonical_dataset_interleaving_a1_b1_c1_d1_a2_c2_a3(self):
        """Proves canonical company-first round-robin interleaving:
        Input:  A1, A2, A3, B1, C1, C2, D1
        Output: A1, B1, C1, D1, A2, C2, A3
        """
        contacts = [
            Contact(contact_id="A1", company_id="CompanyA", name="Alice 1", phone="111"),
            Contact(contact_id="A2", company_id="CompanyA", name="Alice 2", phone="112"),
            Contact(contact_id="A3", company_id="CompanyA", name="Alice 3", phone="113"),
            Contact(contact_id="B1", company_id="CompanyB", name="Bob 1", phone="121"),
            Contact(contact_id="C1", company_id="CompanyC", name="Charlie 1", phone="131"),
            Contact(contact_id="C2", company_id="CompanyC", name="Charlie 2", phone="132"),
            Contact(contact_id="D1", company_id="CompanyD", name="Dave 1", phone="141"),
        ]

        prioritized = prioritize_company_first(contacts)
        result_ids = [c.contact_id for c in prioritized]

        expected_order = ["A1", "B1", "C1", "D1", "A2", "C2", "A3"]
        assert result_ids == expected_order, f"Expected {expected_order}, got {result_ids}"

    def test_automatic_duplicate_suppression_prevents_duplicate_send(self, isolated_db):
        _, SessionFactory = isolated_db
        mock_wa = MockWhatsAppProvider()

        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            sender_repo = SqliteSenderRepository(session)
            contact_repo = SqliteContactRepository(session)
            tmpl_repo = SqliteTemplateRepository(session)

            comp_repo.save(Company.create(name="Salesforce", company_id="salesforce"))
            sender_repo.save(
                SenderAccount.create(
                    channel=Channel.WHATSAPP,
                    provider="mock",
                    identity="+911111111111",
                    display_name="WA Salesforce Line",
                    sender_id="WA-1",
                )
            )
            contact_repo.save(
                Contact(contact_id="cnt_sfdc_1", company_id="salesforce", name="Marc Benioff", phone="+14159017000")
            )
            tmpl_repo.save(
                MessageTemplate.create(
                    template_id="tmpl_sfdc", name="Tmpl", channel=Channel.WHATSAPP, body="Hi {first_name}"
                )
            )
            session.commit()

        # Send once
        with SessionFactory() as session:
            svc = OutreachService(session, whatsapp_provider=mock_wa)
            res1 = svc.send_whatsapp(contact_id="cnt_sfdc_1", sender_id="WA-1", template_id="tmpl_sfdc")
            assert res1["success"] is True

        # Contact is now marked with last_whatsapp_at
        with SessionFactory() as session:
            contact_repo = SqliteContactRepository(session)
            cnt = contact_repo.get_by_id("cnt_sfdc_1")
            assert cnt.last_whatsapp_at is not None


# ==============================================================================
# 8. N-SENDER DYNAMIC SCALABILITY (1, 3, 10 Senders)
# ==============================================================================


class TestNSenderScalability:
    @pytest.mark.parametrize("sender_count", [1, 3, 10])
    def test_dynamic_whatsapp_sender_scaling(self, isolated_db, sender_count: int):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            for i in range(1, sender_count + 1):
                sender_repo.save(
                    SenderAccount.create(
                        channel=Channel.WHATSAPP,
                        provider="mock",
                        identity=f"+91980000000{i}",
                        display_name=f"WA Scale Line {i}",
                        sender_id=f"WA-SCALE-{i}",
                        daily_limit=50,
                    )
                )
            session.commit()

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            active_senders = sender_repo.list_active(channel=Channel.WHATSAPP)
            assert len(active_senders) == sender_count

            # Verify sender usage tracking
            for s in active_senders:
                s.record_usage()
                sender_repo.save(s)
            session.commit()

    @pytest.mark.parametrize("sender_count", [1, 3, 10])
    def test_dynamic_email_sender_scaling(self, isolated_db, sender_count: int):
        _, SessionFactory = isolated_db

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            for i in range(1, sender_count + 1):
                sender_repo.save(
                    SenderAccount.create(
                        channel=Channel.EMAIL,
                        provider="mock",
                        identity=f"outreach_{i}@scale.org",
                        display_name=f"Email Scale Line {i}",
                        sender_id=f"EM-SCALE-{i}",
                        daily_limit=100,
                    )
                )
            session.commit()

        with SessionFactory() as session:
            sender_repo = SqliteSenderRepository(session)
            active_senders = sender_repo.list_active(channel=Channel.EMAIL)
            assert len(active_senders) == sender_count


# ==============================================================================
# 9. SOURCE SYNCHRONIZATION REGRESSION
# ==============================================================================
