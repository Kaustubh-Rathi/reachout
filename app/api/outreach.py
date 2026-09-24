"""Outreach messaging API endpoints.

Handles manual sends, resends, historical audit trails, and recovery queue management.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.domain.enums import Channel
from app.services.outreach_service import OutreachService

router = APIRouter(prefix="/api/outreach", tags=["Outreach"])
FAILED_EXPORT_LIMIT = 5_000
FAILED_EXPORT_CELL_LIMIT = 1_000


def _csv_safe(value: Any) -> str:
    text = ("" if value is None else str(value))[:FAILED_EXPORT_CELL_LIMIT]
    stripped = text.lstrip(" \t\r\n")
    if text.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


class PreviewMessageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: str = Field(..., min_length=1)
    channel: str = Field(..., description="'WHATSAPP' or 'EMAIL'")
    template_id: Optional[str] = None
    is_resend: bool = False


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


@router.post("/preview")
def preview_message(payload: PreviewMessageRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Render the canonical outbound body/subject/attachment for a template + contact."""
    svc = OutreachService(session)
    try:
        channel = Channel(payload.channel.upper())
    except ValueError as exc:
        from app.domain.errors import ValidationError

        raise ValidationError(f"Invalid channel '{payload.channel}'") from exc
    return svc.preview_message(
        contact_id=payload.contact_id,
        channel=channel,
        template_id=payload.template_id,
        is_resend=payload.is_resend,
    )


@router.get("/history/{contact_id}")
def get_contact_history(contact_id: str, session: Session = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Retrieve full chronological outreach attempts for a contact."""
    svc = OutreachService(session)
    return svc.get_history(contact_id)


@router.get("/failures")
def get_failed_attempts(
    campaign_id: Optional[str] = None,
    channel: Optional[str] = None,
    failure_code: Optional[str] = None,
    search: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        parsed_channel = Channel(channel.upper()) if channel else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Channel must be WHATSAPP or EMAIL") from exc
    return OutreachService(session).get_failed_attempts(
        campaign_id=campaign_id,
        channel=parsed_channel,
        failure_code=failure_code,
        search=search,
        offset=offset,
        limit=limit,
    )


@router.get("/failures/export/csv")
def export_failed_attempts_csv(
    campaign_id: Optional[str] = None,
    channel: Optional[str] = None,
    failure_code: Optional[str] = None,
    search: Optional[str] = None,
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    try:
        parsed_channel = Channel(channel.upper()) if channel else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Channel must be WHATSAPP or EMAIL") from exc
    service = OutreachService(session)
    result = service.get_failed_attempts(
        campaign_id=campaign_id,
        channel=parsed_channel,
        failure_code=failure_code,
        search=search,
        offset=0,
        limit=FAILED_EXPORT_LIMIT + 1,
        stop_after=FAILED_EXPORT_LIMIT + 1,
        failure_detail_limit=FAILED_EXPORT_CELL_LIMIT,
    )
    if result["count"] > FAILED_EXPORT_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=f"Failed-attempt exports are limited to {FAILED_EXPORT_LIMIT} rows. Narrow the filters or archive old attempts.",
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Completed At",
            "Contact",
            "Company",
            "Campaign ID",
            "Channel",
            "Destination",
            "Failure Class",
            "Failure Code",
            "Failure Detail",
        ]
    )
    for item in result["items"]:
        writer.writerow(
            [
                _csv_safe(item["completed_at"]),
                _csv_safe(item["contact_name"]),
                _csv_safe(item["company_name"]),
                _csv_safe(item["campaign_id"]),
                _csv_safe(item["channel"]),
                _csv_safe(item["destination"]),
                _csv_safe(item["failure_class"]),
                _csv_safe(item["failure_code"]),
                _csv_safe(item["failure_detail"]),
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter(buffer),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reachout_failed_attempts.csv"},
    )


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
