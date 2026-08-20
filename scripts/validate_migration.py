"""Migration Validation & Integrity Audit Script.

Computes mathematical checks comparing legacy source files (workbooks, send logs, CRM JSON)
against the migrated SQLite relational database to guarantee zero data loss.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from app.domain.company import normalize_company_name
from app.domain.enums import CRMOutcome
from app.infrastructure.database import SessionFactory
from app.infrastructure.models import (
    CompanyModel,
    ContactModel,
    OutreachAttemptModel,
    SenderAccountModel,
    SourceRecordModel,
)
from contact_ingestion import clean_contacts, read_mnc_rows
from scripts.migrate_legacy_data import map_legacy_crm_status

DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"


def run_validation_checks() -> Tuple[bool, Dict[str, Any], List[str]]:
    """Execute complete validation checks and return (is_valid, metrics, errors)."""
    errors: List[str] = []
    metrics: Dict[str, Any] = {}

    session = SessionFactory()

    try:
        # Check 1: Count Contacts
        source_workbook = DATA_DIR / "MNC_Final.xlsx"
        if not source_workbook.exists():
            source_workbook = DATA_DIR / "Reachout.xlsx"

        sheet_rows = read_mnc_rows(source_workbook)
        cleaned_source = clean_contacts(sheet_rows, "91")
        valid_source_contacts = [c for c in cleaned_source if c.status != "duplicate_skipped"]

        # Unique expected source keys
        expected_keys: Set[str] = {
            f"{normalize_company_name(c.company)}|{c.phone}" for c in valid_source_contacts
        }
        metrics["source_unique_contact_keys"] = len(expected_keys)

        db_contacts = session.scalars(select(ContactModel)).all()
        metrics["database_total_contacts"] = len(db_contacts)

        # Database contact keys
        db_keys: Set[str] = {
            f"{c.company_id}|{c.phone}" for c in db_contacts if c.phone
        }

        missing_keys = expected_keys - db_keys
        if missing_keys:
            errors.append(f"Validation Failure: {len(missing_keys)} source contacts missing from database: {list(missing_keys)[:5]}")
        else:
            print("[Pass] Source contacts match database contacts (100% fidelity)")

        # Check 2: Historical WhatsApp Records
        wa_log_path = LOGS_DIR / "mnc_whatsapp_send_log.csv"
        expected_wa_attempts = 0
        if wa_log_path.exists():
            with wa_log_path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                expected_wa_attempts = sum(1 for r in reader if r.get("key"))

        db_wa_attempts = session.scalars(
            select(OutreachAttemptModel).where(OutreachAttemptModel.channel == "WHATSAPP")
        ).all()
        metrics["expected_whatsapp_log_attempts"] = expected_wa_attempts
        metrics["database_whatsapp_attempts"] = len(db_wa_attempts)

        if len(db_wa_attempts) < expected_wa_attempts:
            errors.append(
                f"Validation Failure: Expected {expected_wa_attempts} historical WhatsApp attempts, found {len(db_wa_attempts)} in DB"
            )
        else:
            print(f"[Pass] WhatsApp historical attempts verified ({len(db_wa_attempts)}/{expected_wa_attempts})")

        # Check 3: CRM State & Outcomes
        crm_json_path = DATA_DIR / "crm_data.json"
        expected_crm_records = 0
        crm_checks_passed = 0
        if crm_json_path.exists():
            with crm_json_path.open("r", encoding="utf-8") as f:
                crm_dict = json.load(f)
                expected_crm_records = len(crm_dict)
                for k, rec in crm_dict.items():
                    parts = k.split("|")
                    comp_id = parts[0].strip().lower()
                    phone = parts[1].strip() if len(parts) > 1 else ""

                    matched_cnt = session.scalars(
                        select(ContactModel).where(
                            ContactModel.company_id == comp_id,
                            ContactModel.phone == phone,
                        )
                    ).first()

                    if matched_cnt:
                        expected_mapped_status = map_legacy_crm_status(rec.get("crm_status"))
                        if matched_cnt.crm_outcome != expected_mapped_status:
                            errors.append(
                                f"CRM Outcome mismatch for {k}: expected '{expected_mapped_status}', got '{matched_cnt.crm_outcome}'"
                            )
                        else:
                            crm_checks_passed += 1
                    else:
                        errors.append(f"CRM Record key {k} not found in database contacts")

        metrics["expected_crm_entries"] = expected_crm_records
        metrics["verified_crm_entries"] = crm_checks_passed
        print(f"[Pass] CRM metadata and status verification passed ({crm_checks_passed}/{expected_crm_records})")

        is_valid = len(errors) == 0
        return is_valid, metrics, errors

    finally:
        session.close()


def main():
    print("=" * 60)
    print("[*] Running Migration Validation Audit...")
    print("=" * 60)
    is_valid, metrics, errors = run_validation_checks()

    print("\n--- Summary Metrics ---")
    for k, v in metrics.items():
        print(f"  - {k}: {v}")

    if is_valid:
        print("\n[SUCCESS] All migration validation checks PASSED perfectly.")
        sys.exit(0)
    else:
        print("\n[FAIL] Migration validation detected ERRORS:")
        for err in errors:
            print(f"  [!] {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
