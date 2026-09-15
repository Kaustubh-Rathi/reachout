"""Contacts API endpoints.

Handles listing, searching, filtering, detail viewing, updates, and tombstone archival.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.infrastructure.database import get_session
from app.services.contact_service import ContactService

router = APIRouter(prefix="/api/contacts", tags=["Contacts"])


@router.get("")
def list_contacts(
    search: Optional[str] = None,
    company: Optional[str] = None,
    crm_status: Optional[str] = None,
    send_status: Optional[str] = None,
    priority_filter: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    """Retrieve contacts with full search, filtering, and deterministic priority ordering."""
    svc = ContactService(session)
    return svc.list_contacts(
        search=search,
        company=company,
        crm_status=crm_status,
        send_status=send_status,
        priority_filter=priority_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/discrepancies")
def get_contact_discrepancies(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """List cross-company data discrepancies (same phone/email owned by multiple contacts)."""
    svc = ContactService(session)
    return svc.get_discrepancies()


@router.get("/{contact_id}")
def get_contact(contact_id: str, session: Session = Depends(get_session)):
    """Retrieve full detail for a single contact including attempt timeline and reminders."""
    svc = ContactService(session)
    detail = svc.get_contact_detail(contact_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
    return detail


@router.delete("/{contact_id}")
def archive_contact(contact_id: str, reason: str = "MANUAL_CRM_DELETION", session: Session = Depends(get_session)):
    """Archive/delete a contact and record tombstone suppression."""
    svc = ContactService(session)
    success = svc.archive_contact(contact_id, reason=reason)
    if not success:
        raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
    return {"status": "success", "archived_contact_id": contact_id}


@router.get("/export/csv")
def export_contacts_csv(
    search: Optional[str] = None,
    company: Optional[str] = None,
    crm_status: Optional[str] = None,
    priority_filter: Optional[str] = None,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Export contacts to a CSV download backed by the live database."""
    svc = ContactService(session)
    result = svc.list_contacts(
        search=search,
        company=company,
        crm_status=crm_status,
        priority_filter=priority_filter,
        limit=None,
    )
    contacts = result["contacts"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Company",
            "Name",
            "Designation",
            "Phone",
            "Email",
            "CRM Status",
            "Interview Status",
            "Last Contacted",
            "Follow-up Due",
            "Interested At",
            "Notes",
            "Source Row",
        ]
    )
    for c in contacts:
        writer.writerow(
            [
                c.get("company", ""),
                c.get("name", ""),
                c.get("designation", ""),
                c.get("phone", ""),
                c.get("email", ""),
                c.get("crm_outcome", ""),
                c.get("interview_status", ""),
                c.get("last_contacted") or "",
                c.get("follow_up_due_at") or "",
                c.get("interested_at") or "",
                c.get("notes", ""),
                c.get("source_row", ""),
            ]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reachout_contacts.csv"},
    )
