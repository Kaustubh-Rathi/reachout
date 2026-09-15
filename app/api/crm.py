"""CRM Outcome & Follow-Up Reminders API endpoints.

Handles transitions for Interested/Not Interested, Interview/Not Interview,
conversation notes, and follow-up reminder evaluation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.infrastructure.database import get_session
from app.services.crm_service import CrmService

router = APIRouter(prefix="/api/crm", tags=["CRM"])


class ContactActionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)


class UpdateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)


class UpdateNotesRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)
    notes: str = Field(..., max_length=5000)


@router.post("/status")
def update_status(payload: UpdateStatusRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Update contact CRM business status directly from CRM control plane."""
    svc = CrmService(session)
    try:
        return svc.update_status(payload.contact_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/interested")
def mark_interested(payload: ContactActionRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Mark contact as interested. Records interested_at and transitions interview to PENDING."""
    svc = CrmService(session)
    try:
        return svc.mark_interested(payload.contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/not-interested")
def mark_not_interested(payload: ContactActionRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Mark contact as not interested. Moves contact to bottom tier and clears reminders."""
    svc = CrmService(session)
    try:
        return svc.mark_not_interested(payload.contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/interview")
def mark_interview(payload: ContactActionRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Mark interview scheduled/progressing. Follow-up reminder is no longer due."""
    svc = CrmService(session)
    try:
        return svc.mark_interview(payload.contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/not-interview")
def mark_not_interview(payload: ContactActionRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Mark not interviewing / rejected. Follow-up reminder is no longer due."""
    svc = CrmService(session)
    try:
        return svc.mark_not_interview(payload.contact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/notes")
def update_notes(payload: UpdateNotesRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Update conversation notes for contact."""
    svc = CrmService(session)
    try:
        return svc.update_notes(payload.contact_id, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/reminders")
def list_reminders(
    only_due: bool = Query(False, description="Filter only to reminders currently due"),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """List pending/due follow-up reminders."""
    svc = CrmService(session)
    return svc.list_reminders(only_due=only_due)


@router.post("/reminders/generate")
def generate_reminders(
    threshold_days: int = Query(7, ge=1, le=90),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Scan interested contacts and generate due follow-up reminders."""
    svc = CrmService(session)
    return svc.generate_due_reminders(threshold_days=threshold_days)


@router.get("/kpis")
def get_kpis(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve mathematically verified KPI statistics."""
    svc = CrmService(session)
    return svc.get_kpis()
