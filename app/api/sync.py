"""Source synchronization API endpoints.

Handles non-destructive ingestion and reconciliation from external workbooks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.services.sync_service import SyncService

router = APIRouter(prefix="/api/sync", tags=["Sync"])


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_path: Optional[str] = None
    sheet_name: Optional[str] = None


@router.post("")
def sync_contacts(payload: SyncRequest = SyncRequest(), session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Trigger non-destructive synchronization from external Excel/CSV."""
    svc = SyncService(session)
    try:
        return svc.sync_source(source_path=payload.source_path, sheet_name=payload.sheet_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}") from exc


@router.get("/summary")
def get_sync_summary(session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Retrieve outcome metrics from the most recent sync."""
    svc = SyncService(session)
    return svc.get_last_sync_summary()
