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

import hashlib
from pathlib import Path

import pytest

from app.domain.company import Company
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository

# ==============================================================================
# 1. DATABASE ISOLATION & IMMUTABILITY VERIFICATION
# ==============================================================================


class TestDatabaseIsolationAndImmutability:
    def test_production_db_remains_untouched_by_isolated_operations(self, isolated_db):
        prod_db_path = Path("data/reachout.db")
        if not prod_db_path.exists():
            pytest.skip("Production database not present on disk")

        pre_hash = hashlib.sha256(prod_db_path.read_bytes()).hexdigest()

        # Perform mutations on isolated database
        _, SessionFactory = isolated_db
        with SessionFactory() as session:
            comp_repo = SqliteCompanyRepository(session)
            comp_repo.save(Company.create(name="Isolation Test Co", company_id="iso-co"))
            session.commit()

        post_hash = hashlib.sha256(prod_db_path.read_bytes()).hexdigest()
        assert pre_hash == post_hash, "Production reachout.db was mutated during test execution!"


# ==============================================================================
# 2. MANUAL SEND & RESEND REGRESSION (P0 Fix Verification)
# ==============================================================================
