"""Automated test suite for Reachout CRM pipeline, security, and API."""

from __future__ import annotations

import csv
import io
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from fastapi.testclient import TestClient

from contact_ingestion import (
    ALL_CRM_STATUSES,
    CRM_DB_FILE,
    DATA_DIR,
    DEFAULT_WORKBOOK,
    FOLLOW_UP_STATUSES,
    NO_HIRING_STATUSES,
    POSITIVE_STATUSES,
    REPLY_STATUSES,
    UNREPLIED_STATUSES,
    Contact,
    clean_contacts,
    extract_numbers,
    first_name,
    normalize_phone,
    read_mnc_rows,
    render_message,
    sanitize_for_csv,
    write_clean_csv,
)
from crm_server import (
    CRM_BACKUP_FILE,
    BulkContactUpdate,
    ContactUpdate,
    app,
    bulk_update_contacts,
    export_crm_csv,
    get_merged_contacts,
    get_stats,
    list_contacts,
    load_crm_db,
    save_crm_db,
    update_contact,
)
import clean_mnc

client = TestClient(app)


# ==========================================
# Test Group A — Phone Normalization
# ==========================================

class TestPhoneNormalization:
    def test_10_digit_indian_number(self):
        assert normalize_phone("9876543210", "91") == "919876543210"

    def test_11_digit_with_leading_zero(self):
        assert normalize_phone("09876543210", "91") == "919876543210"

    def test_12_digit_with_country_code(self):
        assert normalize_phone("919876543210", "91") == "919876543210"
        assert normalize_phone("+919876543210", "91") == "919876543210"

    def test_international_us_number(self):
        assert normalize_phone("+12627491577", "91") == "12627491577"
        assert normalize_phone("0012627491577", "91") == "12627491577"

    def test_excel_float_notation(self):
        assert normalize_phone("8826363651.0", "91") == "918826363651"
        assert normalize_phone(8826363651.0, "91") == "918826363651"

    def test_invalid_and_empty_values(self):
        assert normalize_phone("", "91") is None
        assert normalize_phone(None, "91") is None
        assert normalize_phone("123", "91") is None
        assert normalize_phone("abcdef", "91") is None

    def test_multi_number_cell_extraction(self):
        raw = "+919980656407, +916364866859"
        numbers = extract_numbers(raw, "91")
        assert numbers == ["919980656407", "916364866859"]

        raw_mixed = "+918046216510; +919742244403 / +12627491577"
        numbers_mixed = extract_numbers(raw_mixed, "91")
        assert numbers_mixed == ["918046216510", "919742244403", "12627491577"]


# ==========================================
# Test Group B — Contact Ingestion & Email Fidelity
# ==========================================

class TestContactIngestion:
    @pytest.fixture
    def parsed_contacts(self):
        rows = read_mnc_rows(DEFAULT_WORKBOOK)
        return clean_contacts(rows, "91")

    def test_ingestion_fields_populated(self, parsed_contacts):
        assert len(parsed_contacts) > 0
        first = parsed_contacts[0]
        assert first.company != ""
        assert first.name != ""
        assert first.phone != ""
        assert first.source_row >= 2
        assert first.slot != ""
        assert first.status in ("ready", "duplicate_conflict", "duplicate_skipped")

    def test_email_preservation_for_known_records(self, parsed_contacts):
        indeed_contacts = [c for c in parsed_contacts if c.company.lower() == "indeed"]
        assert len(indeed_contacts) > 0
        assert any(c.email == "vinishadesouza@gmail.com" for c in indeed_contacts)

        # Missing email defaults to empty string, not None or fake data
        razorpay = next(c for c in parsed_contacts if c.company.lower() == "razorpay")
        assert razorpay.email == ""


# ==========================================
# Test Group C — Duplicate Handling & Invariant
# ==========================================

class TestDuplicateHandling:
    def test_duplicate_skipped_identified(self):
        rows = read_mnc_rows(DEFAULT_WORKBOOK)
        cleaned = clean_contacts(rows, "91")
        skipped = [c for c in cleaned if c.status == "duplicate_skipped"]
        assert len(skipped) >= 1
        assert any("netapp" in c.company.lower() for c in skipped)

    def test_merged_contacts_unique_keys_invariant(self):
        merged = get_merged_contacts()
        keys = [c["key"] for c in merged]
        assert len(keys) == len(set(keys)), "Every contact key in merged output must be strictly unique"

    def test_duplicate_conflicts_surfaced_with_detail(self):
        merged = get_merged_contacts()
        conflicts = [c for c in merged if c["dedup_status"] == "duplicate_conflict"]
        assert len(conflicts) == 4
        for c in conflicts:
            assert c["dedup_detail"] != "", "Conflict detail must explain why the number is ambiguous"
            assert "assigned to multiple contacts" in c["dedup_detail"]


# ==========================================
# Test Group D — CSV Export & Formula Injection Security
# ==========================================

class TestCSVExportAndSecurity:
    def test_email_in_merged_and_api(self):
        contacts_resp = list_contacts()
        indeed = next((c for c in contacts_resp["contacts"] if c["company"].lower() == "indeed"), None)
        assert indeed is not None
        assert indeed["email"] == "vinishadesouza@gmail.com"

    def test_email_in_csv_export(self):
        import asyncio
        resp = export_crm_csv()

        async def read_stream():
            chunks = []
            async for chunk in resp.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        content = asyncio.run(read_stream())
        reader = list(csv.DictReader(io.StringIO(content)))
        assert "email" in reader[0]
        indeed_rows = [r for r in reader if r["company"].lower() == "indeed"]
        assert any(r["email"] == "vinishadesouza@gmail.com" for r in indeed_rows)

    def test_csv_formula_injection_sanitization(self):
        # Formula prefixes: =, +, -, @, \t, \r, %
        assert sanitize_for_csv("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
        assert sanitize_for_csv("+SUM(1,2)") == "'+SUM(1,2)"
        assert sanitize_for_csv("-100") == "'-100"
        assert sanitize_for_csv("@HYPERLINK('http://evil.com')") == "'@HYPERLINK('http://evil.com')"
        assert sanitize_for_csv("\tTabbed") == "'\tTabbed"
        assert sanitize_for_csv("%Special") == "'%Special"
        assert sanitize_for_csv("Normal Text") == "Normal Text"
        assert sanitize_for_csv("") == ""
        assert sanitize_for_csv(None) == ""

    def test_csv_export_neutralizes_formulas(self):
        import asyncio
        test_key = "razorpay|918826363651"
        malicious_note = "=cmd|' /C calc'!A0"
        update_contact(ContactUpdate(
            key=test_key,
            notes=malicious_note,
            crm_status="Replied - Interested"
        ))

        try:
            resp = export_crm_csv()
            async def read_stream():
                chunks = []
                async for chunk in resp.body_iterator:
                    chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
                return "".join(chunks)

            content = asyncio.run(read_stream())
            reader = list(csv.DictReader(io.StringIO(content)))
            razorpay_row = next(r for r in reader if r["key"] == test_key)
            assert razorpay_row["notes"] == f"'{malicious_note}", "CSV export must prefix single quote to neutralize formulas"
        finally:
            update_contact(ContactUpdate(key=test_key, notes="", crm_status="No Status"))


# ==========================================
# Test Group E — KPI Mathematics
# ==========================================

class TestKPIMathematics:
    def test_response_rate_cases(self):
        # Case 1: Standard delivery
        sent = 10
        sent_text_only = 0
        replies = 3
        delivered = sent + sent_text_only
        rate = round((replies / delivered) * 100, 1) if delivered > 0 else 0.0
        assert rate == 30.0

        # Case 2: Mixed delivery (Sent + Text Only)
        sent = 7
        sent_text_only = 3
        replies = 2
        delivered = sent + sent_text_only
        rate = round((replies / delivered) * 100, 1) if delivered > 0 else 0.0
        assert rate == 20.0

        # Case 3: Zero delivery
        sent = 0
        sent_text_only = 0
        replies = 0
        delivered = sent + sent_text_only
        rate = round((replies / delivered) * 100, 1) if delivered > 0 else 0.0
        assert rate == 0.0

    def test_stats_endpoint_math(self):
        stats = get_stats()
        assert stats["total"] == len(get_merged_contacts())
        assert stats["delivered"] == stats["sent"] + stats["sent_text_only"]
        if stats["delivered"] > 0:
            expected_rate = round((stats["replies"] / stats["delivered"]) * 100, 1)
            assert stats["response_rate"] == expected_rate
        else:
            assert stats["response_rate"] == 0.0


# ==========================================
# Test Group F — CRM Status Semantics
# ==========================================

class TestStatusSemantics:
    def test_not_hiring_freshers_classification(self):
        assert "Not Hiring Freshers" in REPLY_STATUSES
        assert "Not Hiring Freshers" in NO_HIRING_STATUSES
        assert "Not Hiring Freshers" not in POSITIVE_STATUSES

    def test_positive_status_classification(self):
        assert "Replied - Interested" in POSITIVE_STATUSES
        assert "Referral Given" in POSITIVE_STATUSES
        assert "Interview Scheduled" in POSITIVE_STATUSES
        assert "Call Scheduled" in POSITIVE_STATUSES

    def test_unreplied_status_classification(self):
        assert "No Status" in UNREPLIED_STATUSES
        assert "Pending Reply" in UNREPLIED_STATUSES
        assert "Ghosted / No Reply" in UNREPLIED_STATUSES


# ==========================================
# Test Group G — Persistence, Recovery & Concurrency
# ==========================================

class TestPersistenceAndConcurrency:
    def test_single_contact_update_and_persistence(self):
        test_key = "razorpay|918826363651"
        res = update_contact(
            ContactUpdate(
                key=test_key,
                crm_status="Replied - Interested",
                notes="Automated test note",
                follow_up_date="2026-09-01",
                tags=["test_tag", "fintech"],
            )
        )
        assert res["status"] == "success"
        assert res["record"]["crm_status"] == "Replied - Interested"
        assert res["record"]["notes"] == "Automated test note"

        # Verify persisted in database
        db = load_crm_db()
        assert db[test_key]["crm_status"] == "Replied - Interested"
        assert db[test_key]["notes"] == "Automated test note"
        assert db[test_key]["follow_up_date"] == "2026-09-01"

        # Revert back to clean state
        update_contact(
            ContactUpdate(
                key=test_key,
                crm_status="No Status",
                notes="",
                follow_up_date="",
                tags=[],
            )
        )

    def test_database_backup_and_corrupt_recovery(self):
        # Save valid state
        valid_data = load_crm_db()
        save_crm_db(valid_data)
        assert CRM_BACKUP_FILE.exists()

        # Simulate corrupt primary file
        orig_content = CRM_DB_FILE.read_text(encoding="utf-8")
        try:
            CRM_DB_FILE.write_text("{ corrupt json ...", encoding="utf-8")
            recovered = load_crm_db()
            assert isinstance(recovered, dict)
            assert len(recovered) == len(valid_data)
        finally:
            CRM_DB_FILE.write_text(orig_content, encoding="utf-8")

    def test_concurrent_multithreaded_updates(self):
        keys = ["razorpay|918826363651", "wells fargo|919980656407", "flipkart|919370155113"]
        errors = []

        def worker(key, idx):
            try:
                update_contact(
                    ContactUpdate(
                        key=key,
                        notes=f"Concurrent update {idx}",
                        tags=[f"thread_{idx}"],
                    )
                )
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(15):
            k = keys[i % len(keys)]
            t = threading.Thread(target=worker, args=(k, i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors encountered: {errors}"

        # Clean up test keys
        for k in keys:
            update_contact(ContactUpdate(key=k, crm_status="No Status", notes="", follow_up_date="", tags=[]))


# ==========================================
# Test Group H — Strict API Validation & Contracts
# ==========================================

class TestAPIContracts:
    def test_list_contacts_response(self):
        res = client.get("/api/contacts").json()
        assert "count" in res
        assert "contacts" in res
        assert res["count"] == len(res["contacts"])
        assert res["count"] == len(get_merged_contacts())

    def test_get_stats_response(self):
        res = client.get("/api/stats").json()
        required_keys = {
            "workbook_name", "total", "sent", "sent_text_only", "delivered",
            "not_sent", "failed", "replies", "response_rate", "interested",
            "no_hiring", "follow_ups", "companies", "statuses"
        }
        assert required_keys.issubset(res.keys())
        assert res["total"] == len(get_merged_contacts())
        assert len(res["statuses"]) == len(ALL_CRM_STATUSES)

    def test_bulk_update_contacts(self):
        keys = ["razorpay|918826363651", "flipkart|919370155113"]
        res = client.post("/api/contacts/bulk-update", json={"keys": keys, "crm_status": "Follow-up Needed"}).json()
        assert res["status"] == "success"
        assert res["updated_count"] == 2

        db = load_crm_db()
        assert db["razorpay|918826363651"]["crm_status"] == "Follow-up Needed"
        assert db["flipkart|919370155113"]["crm_status"] == "Follow-up Needed"

        # Revert
        client.post("/api/contacts/bulk-update", json={"keys": keys, "crm_status": "No Status"})

    def test_api_validation_rejects_invalid_status(self):
        resp = client.post("/api/contacts/update", json={"key": "razorpay|918826363651", "crm_status": "HackedStatus"})
        assert resp.status_code == 422, "API must reject status not in ALL_CRM_STATUSES"

    def test_api_validation_rejects_invalid_date(self):
        resp = client.post("/api/contacts/update", json={"key": "razorpay|918826363651", "follow_up_date": "not-a-date"})
        assert resp.status_code == 422, "API must reject invalid calendar date format"

    def test_api_validation_rejects_malformed_key(self):
        resp = client.post("/api/contacts/update", json={"key": "no_pipe_symbol", "crm_status": "Pending Reply"})
        assert resp.status_code == 422, "API must reject key without company|phone format"

        resp_empty = client.post("/api/contacts/update", json={"key": "", "crm_status": "Pending Reply"})
        assert resp_empty.status_code == 422, "API must reject empty key string"

    def test_api_validation_rejects_empty_bulk_keys(self):
        resp = client.post("/api/contacts/bulk-update", json={"keys": [], "crm_status": "Pending Reply"})
        assert resp.status_code == 422, "API must reject empty keys list in bulk update"

    def test_api_validation_rejects_extra_fields(self):
        resp = client.post("/api/contacts/update", json={"key": "razorpay|918826363651", "unknown_field": "injected"})
        assert resp.status_code == 422, "API must forbid extra unknown fields"


# ==========================================
# Test Group I — Clean MNC & Automation Regression
# ==========================================

class TestCleanMNCAndAutomation:
    def test_clean_mnc_runs_and_extracts(self, tmp_path):
        out_file = tmp_path / "test_mnc_clean.xlsx"
        rows = read_mnc_rows(DEFAULT_WORKBOOK)
        contacts = clean_contacts(rows, "91")
        saved = clean_mnc.save_to_excel(out_file, contacts)
        assert saved.exists()
        assert saved.stat().st_size > 0

    def test_message_rendering_template(self):
        c = Contact(
            source_row=10,
            company="Google",
            name="Sundar Pichai",
            phone="919876543210"
        )
        msg = render_message(c)
        assert "Hey Sundar," in msg
        assert "*Google*" in msg
        assert "Kaustubh Rathi" in msg
