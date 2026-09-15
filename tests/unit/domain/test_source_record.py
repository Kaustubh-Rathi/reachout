"""Unit tests for SourceRecord domain model and source lineage."""

from datetime import datetime, timedelta, timezone

from app.domain.source_record import SourceRecord, compute_source_fingerprint


class TestSourceRecordModel:
    def test_fingerprint_determinism(self):
        fp1 = compute_source_fingerprint(
            source_file="MNC_Final.xlsx",
            source_sheet="MNC_Cleaned",
            source_row=12,
            raw_payload={"company": "Google", "name": "John Doe", "phone": "919999999999"},
        )
        fp2 = compute_source_fingerprint(
            source_file="MNC_Final.xlsx",
            source_sheet="MNC_Cleaned",
            source_row=12,
            raw_payload={"company": "Google", "name": "John Doe", "phone": "919999999999"},
        )
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex length

    def test_source_record_creation(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        record = SourceRecord.create(
            source_file="MNC_Final.xlsx",
            source_sheet="MNC_Cleaned",
            source_row=45,
            raw_payload={"A": "Amazon", "B": "Alice"},
            observed_at=t0,
        )
        assert record.source_file == "MNC_Final.xlsx"
        assert record.source_sheet == "MNC_Cleaned"
        assert record.source_row == 45
        assert record.first_seen_at == t0
        assert record.last_seen_at == t0

    def test_with_updated_observation(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        record = SourceRecord.create(
            source_file="Reachout.xlsx",
            source_row=10,
            observed_at=t0,
        )
        t1 = t0 + timedelta(days=5)
        updated = record.with_updated_observation(observed_at=t1)
        assert updated.first_seen_at == t0
        assert updated.last_seen_at == t1
        assert updated.source_row == record.source_row
        assert updated.source_fingerprint == record.source_fingerprint
