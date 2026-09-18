"""Outreach messaging API endpoints.

Handles manual sends, resends, historical audit trails, and recovery queue management.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.services.outreach_service import OutreachService

router = APIRouter(prefix="/api/outreach", tags=["Outreach"])


class SendWhatsAppRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)
    sender_id: Optional[str] = None
    template_id: Optional[str] = None
    custom_body: Optional[str] = None
    attachment_ref: Optional[str] = None
    destination: Optional[str] = None


class SendEmailRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)
    sender_id: Optional[str] = None
    template_id: Optional[str] = None
    subject: Optional[str] = None
    custom_body: Optional[str] = None
    attachment_ref: Optional[str] = None
    destination: Optional[str] = None


class ResolveRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(..., description="'mark_sent', 'retry', or 'cancel'")
    recovery_notes: Optional[str] = None


@router.post("/send-whatsapp")
def send_whatsapp(payload: SendWhatsAppRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Manually dispatch a WhatsApp message to a specific endpoint."""
    svc = OutreachService(session)
    return svc.send_whatsapp(
        contact_id=payload.contact_id,
        sender_id=payload.sender_id,
        template_id=payload.template_id,
        custom_body=payload.custom_body,
        attachment_ref=payload.attachment_ref,
        destination=payload.destination,
    )


@router.post("/send-email")
def send_email(payload: SendEmailRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Manually dispatch an Email message to a specific endpoint."""
    svc = OutreachService(session)
    return svc.send_email(
        contact_id=payload.contact_id,
        sender_id=payload.sender_id,
        template_id=payload.template_id,
        subject=payload.subject,
        custom_body=payload.custom_body,
        attachment_ref=payload.attachment_ref,
        destination=payload.destination,
    )


@router.post("/resend-whatsapp")
def resend_whatsapp(payload: SendWhatsAppRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Trigger manual WhatsApp resend creating a new attempt while preserving history."""
    svc = OutreachService(session)
    return svc.resend_whatsapp(
        contact_id=payload.contact_id,
        sender_id=payload.sender_id,
        template_id=payload.template_id,
        custom_body=payload.custom_body,
        attachment_ref=payload.attachment_ref,
        destination=payload.destination,
    )


@router.post("/resend-email")
def resend_email(payload: SendEmailRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Trigger manual Email resend creating a new attempt while preserving history."""
    svc = OutreachService(session)
    return svc.resend_email(
        contact_id=payload.contact_id,
        sender_id=payload.sender_id,
        template_id=payload.template_id,
        subject=payload.subject,
        custom_body=payload.custom_body,
        attachment_ref=payload.attachment_ref,
        destination=payload.destination,
    )


@router.get("/history/{contact_id}")
def get_contact_history(contact_id: str, session: Session = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Retrieve full chronological outreach attempts for a contact."""
    svc = OutreachService(session)
    return svc.get_history(contact_id)


@router.get("/recovery")
def get_recovery_queue(session: Session = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """List attempts requiring operator recovery or unknown provider statuses."""
    svc = OutreachService(session)
    return svc.get_recovery_queue()


@router.post("/recovery/{attempt_id}/resolve")
def resolve_recovery_attempt(
    attempt_id: str, payload: ResolveRecoveryRequest, session: Session = Depends(get_db_session)
) -> Dict[str, Any]:
    """Resolve a stuck recovery attempt."""
    svc = OutreachService(session)
    return svc.resolve_recovery(
        attempt_id=attempt_id,
        action=payload.action,
        recovery_notes=payload.recovery_notes,
    )
