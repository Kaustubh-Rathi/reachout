"""Source reading and synchronization ports.

Decouples ingestion from raw file formats (Excel XLSX, CSV, Google Sheets).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.domain.source_record import SourceRecord


@dataclass(frozen=True)
class SourceRow:
    """Raw parsed row from an external workbook or CSV file."""
    source_file: str
    source_row: int
    raw_values: Dict[str, str]
    source_sheet: Optional[str] = None
    fingerprint: str = ""

    def to_source_record(self) -> SourceRecord:
        return SourceRecord.create(
            source_file=self.source_file,
            source_sheet=self.source_sheet,
            source_row=self.source_row,
            raw_payload=self.raw_values,
            fingerprint=self.fingerprint or None,
        )


@dataclass(frozen=True)
class SyncSummary:
    """Outcome metrics of a data synchronization cycle."""
    total_read: int
    new_contacts: int
    updated_contacts: int
    unchanged_contacts: int
    new_companies: int = 0
    updated_companies: int = 0
    new_phone_endpoints: int = 0
    new_email_endpoints: int = 0
    skipped_invalid: int = 0
    history_preserved: bool = True
    errors: List[str] = field(default_factory=list)


@runtime_checkable
class SourceReader(Protocol):
    """Port for reading raw tabular rows from source files."""

    def read_source(
        self, source_path: str, sheet_name: Optional[str] = None
    ) -> List[SourceRow]:
        """Read and parse raw records from a file path without mutating it."""
        ...


@runtime_checkable
class SourceSynchronizer(Protocol):
    """Port for ingesting and reconciling source data against the domain store."""

    def sync_source(
        self, source_path: str, sheet_name: Optional[str] = None
    ) -> SyncSummary:
        """Perform non-destructive synchronization from source to domain repository."""
        ...
