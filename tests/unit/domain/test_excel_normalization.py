"""Unit tests for Phase 8 Excel multi-endpoint normalization and sync metrics."""

from app.infrastructure.source.synchronizer import extract_emails, extract_phone_numbers
from app.ports.source import SyncSummary


def test_excel_normalization_multiple_phones_and_emails():
    """Verify phone and email parser normalizes multiple comma/semicolon/newline separated endpoints."""
    raw_phones = "+91 98765 43210, +91 98123 45678 / +91-9988776655"
    raw_emails = "hr1.work@corp.com; hr.personal@gmail.com, hr.alt@corp.org"

    parsed_phones = extract_phone_numbers(raw_phones)
    parsed_emails = extract_emails(raw_emails)

    assert "919876543210" in parsed_phones
    assert "919812345678" in parsed_phones
    assert "919988776655" in parsed_phones

    assert "hr1.work@corp.com" in parsed_emails
    assert "hr.personal@gmail.com" in parsed_emails
    assert "hr.alt@corp.org" in parsed_emails


def test_sync_summary_metrics_structure():
    """Verify SyncSummary data structure tracks all required Phase 8 sync metrics."""
    summary = SyncSummary(
        total_read=50,
        new_companies=5,
        updated_companies=10,
        new_contacts=12,
        updated_contacts=15,
        unchanged_contacts=23,
        new_phone_endpoints=18,
        new_email_endpoints=20,
        skipped_invalid=0,
        history_preserved=True,
    )

    assert summary.total_read == 50
    assert summary.new_companies == 5
    assert summary.updated_companies == 10
    assert summary.new_contacts == 12
    assert summary.updated_contacts == 15
    assert summary.unchanged_contacts == 23
    assert summary.new_phone_endpoints == 18
    assert summary.new_email_endpoints == 20
    assert summary.skipped_invalid == 0
    assert summary.history_preserved is True
