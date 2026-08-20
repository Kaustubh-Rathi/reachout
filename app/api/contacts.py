"""Contacts API endpoints.

Handles listing, searching, filtering, detail viewing, updates, and tombstone archival.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.infrastructure.database import get_session
from app.services.contact_service import ContactService
from app.services.crm_service import CrmService

router = APIRouter(prefix="/api/contacts", tags=["Contacts"])


class ContactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    designation: Optional[str] = Field("", max_length=255)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field("", max_length=5000)
    tags: Optional[List[str]] = Field(default_factory=list)


class ContactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=255)
    designation: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)
    tags: Optional[List[str]] = None


class LegacyContactUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    crm_status: Optional[str] = None
    notes: Optional[str] = None
    follow_up_date: Optional[str] = None
    tags: Optional[List[str]] = None


class LegacyBulkUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keys: List[str]
    crm_status: Optional[str] = None
    tags: Optional[List[str]] = None


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


@router.post("/{contact_id}/archive")
def archive_contact_post(contact_id: str, reason: str = "MANUAL_CRM_DELETION", session: Session = Depends(get_session)):
    """POST endpoint for archiving/deleting a contact."""
    svc = ContactService(session)
    success = svc.archive_contact(contact_id, reason=reason)
    if not success:
        raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
    return {"status": "success", "archived_contact_id": contact_id}

