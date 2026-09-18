"""Source synchronization application service.

Coordinates non-destructive reconciliation of external spreadsheets into the
relational database.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.ports.source import SyncSummary
from app.services.context import ServiceContext, build_service_context


class SyncService:
    """Application service for source synchronization."""

    # Most recent sync summary, cached process-wide (the summary endpoint runs in
    # a separate request/instance from the sync that produced it).
    _last_summary: Optional[SyncSummary] = None
    _summary_lock = threading.Lock()

    def __init__(
        self,
        session: Session,
        context: Optional[ServiceContext] = None,
        synchronizer=None,
    ) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.event_publisher = ctx.event_publisher
        self.synchronizer = synchronizer or ctx.source_synchronizer

    def sync_source(
        self,
        source_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform non-destructive synchronization from source to domain repository."""
        path = source_path
        if not path:
            # Default to MNC_Final.xlsx or Reachout.xlsx in data/
            root = Path(__file__).resolve().parent.parent.parent
            data_dir = root / "data"
            candidate_1 = data_dir / "MNC_Final.xlsx"
            candidate_2 = data_dir / "Reachout.xlsx"
            candidate_3 = data_dir / "mnc_cleaned_contacts.csv"

            if candidate_1.exists():
                path = str(candidate_1)
            elif candidate_2.exists():
                path = str(candidate_2)
            elif candidate_3.exists():
                path = str(candidate_3)
            else:
                raise FileNotFoundError("No default source workbook found in data directory.")

        filename = Path(path).name
        self.event_publisher.publish_event(
            "EXCEL_SYNC_STARTED",
            {"source_file": filename},
        )

        try:
            summary = self.synchronizer.sync_source(path, sheet_name)
            with SyncService._summary_lock:
                SyncService._last_summary = summary

            payload = {
                "source_file": filename,
                "total_read": summary.total_read,
                "new_companies": summary.new_companies,
                "updated_companies": summary.updated_companies,
                "new_contacts": summary.new_contacts,
                "updated_contacts": summary.updated_contacts,
                "unchanged_contacts": summary.unchanged_contacts,
                "new_phone_endpoints": summary.new_phone_endpoints,
                "new_email_endpoints": summary.new_email_endpoints,
                "skipped_invalid": summary.skipped_invalid,
                "history_preserved": summary.history_preserved,
                "errors_count": len(summary.errors),
                "errors": summary.errors,
            }

            self.event_publisher.publish_event("EXCEL_SYNC_COMPLETED", payload)
            self.event_publisher.publish_event("SYNC_COMPLETED", payload)

            return payload
        except Exception as exc:
            self.event_publisher.publish_event(
                "EXCEL_SYNC_FAILED",
                {"source_file": filename, "error": str(exc)},
            )
            raise exc

    def get_last_sync_summary(self) -> Dict[str, Any]:
        """Retrieve the outcome metrics of the most recent sync."""
        with SyncService._summary_lock:
            s = SyncService._last_summary
        if not s:
            return {
                "total_read": 0,
                "new_companies": 0,
                "updated_companies": 0,
                "new_contacts": 0,
                "updated_contacts": 0,
                "unchanged_contacts": 0,
                "new_phone_endpoints": 0,
                "new_email_endpoints": 0,
                "skipped_invalid": 0,
                "history_preserved": True,
                "errors": [],
            }
        return {
            "total_read": s.total_read,
            "new_companies": s.new_companies,
            "updated_companies": s.updated_companies,
            "new_contacts": s.new_contacts,
            "updated_contacts": s.updated_contacts,
            "unchanged_contacts": s.unchanged_contacts,
            "new_phone_endpoints": s.new_phone_endpoints,
            "new_email_endpoints": s.new_email_endpoints,
            "skipped_invalid": s.skipped_invalid,
            "history_preserved": s.history_preserved,
            "errors": s.errors,
        }
