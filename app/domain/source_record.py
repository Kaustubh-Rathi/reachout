"""Source lineage domain model.

Tracks origin file, sheet, row number, content hash, and observation timestamps
to preserve provenance without mutating original source data files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def compute_source_fingerprint(
    source_file: str,
    source_sheet: Optional[str],
    source_row: int,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint for a source row."""
    normalized_file = source_file.strip().lower()
    normalized_sheet = (source_sheet or "").strip().lower()
    payload_str = json.dumps(raw_payload, sort_keys=True) if raw_payload else ""
    seed = f"{normalized_file}:{normalized_sheet}:{source_row}:{payload_str}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRecord:
    """Immutable provenance record linking domain entities to raw data sheets.
    
    Attributes:
        source_file: Relative or base name of source file (e.g. 'MNC_Final.xlsx').
        source_sheet: Sheet or tab name if workbook (e.g. 'MNC_Cleaned').
        source_row: 1-indexed row number in source file (for traceability, not identity).
        source_fingerprint: Deterministic SHA-256 digest of original record content.
        first_seen_at: Timestamp when record was initially ingested.
        last_seen_at: Timestamp when record was most recently verified in source.
    """
    source_file: str
    source_sheet: Optional[str]
    source_row: int
    source_fingerprint: str
    first_seen_at: datetime
    last_seen_at: datetime

    @classmethod
    def create(
        cls,
        source_file: str,
        source_row: int,
        source_sheet: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        observed_at: Optional[datetime] = None,
        fingerprint: Optional[str] = None,
    ) -> SourceRecord:
        now = observed_at or datetime.now(timezone.utc)
        fp = fingerprint or compute_source_fingerprint(source_file, source_sheet, source_row, raw_payload)
        return cls(
            source_file=source_file,
            source_sheet=source_sheet,
            source_row=source_row,
            source_fingerprint=fp,
            first_seen_at=now,
            last_seen_at=now,
        )

    def with_updated_observation(self, observed_at: Optional[datetime] = None) -> SourceRecord:
        """Return a new SourceRecord with updated last_seen_at timestamp."""
        now = observed_at or datetime.now(timezone.utc)
        return SourceRecord(
            source_file=self.source_file,
            source_sheet=self.source_sheet,
            source_row=self.source_row,
            source_fingerprint=self.source_fingerprint,
            first_seen_at=self.first_seen_at,
            last_seen_at=now,
        )
