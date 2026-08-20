"""Templates API endpoints.

Handles message templates configuration, channel filtering, and editing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.domain.enums import Channel
from app.infrastructure.database import get_session
from app.services.template_service import TemplateService

router = APIRouter(prefix="/api/templates", tags=["Templates"])


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    channel: str = Field(..., description="'WHATSAPP' or 'EMAIL'")
    body: str = Field(..., min_length=1)
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None
    phone_number: Optional[str] = None
    active: bool = True


class UpdateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = None
    body: Optional[str] = None
    subject: Optional[str] = None
    attachment_ref: Optional[str] = None
    phone_number: Optional[str] = None
    active: Optional[bool] = None


@router.get("")
def list_templates(
    channel: Optional[str] = Query(None, description="Optional filter by 'WHATSAPP' or 'EMAIL'"),
    active_only: bool = Query(False, description="Filter only active templates"),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """List configured message templates."""
    svc = TemplateService(session)
    templates = svc.list_templates(channel=channel, active_only=active_only)
    return [
        {
            "id": t.id,
            "name": t.name,
            "channel": t.channel.value,
            "body": t.body,
            "subject": t.subject,
            "attachment_ref": t.attachment_ref,
            "phone_number": t.phone_number,
            "active": t.active,
            "created_at": t.created_at.isoformat(),
        }
        for t in templates
    ]


@router.get("/{template_id}")
def get_template(template_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve single template."""
    svc = TemplateService(session)
    t = svc.get_template(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {
        "id": t.id,
        "name": t.name,
        "channel": t.channel.value,
        "body": t.body,
        "subject": t.subject,
        "attachment_ref": t.attachment_ref,
        "phone_number": t.phone_number,
        "active": t.active,
        "created_at": t.created_at.isoformat(),
    }


@router.post("")
def create_template(payload: CreateTemplateRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Create a new message template."""
    svc = TemplateService(session)
    try:
        ch = Channel(payload.channel.upper())
        t = svc.create_template(
            id=payload.id,
            name=payload.name,
            channel=ch,
            body=payload.body,
            subject=payload.subject,
            attachment_ref=payload.attachment_ref,
            phone_number=payload.phone_number,
            active=payload.active,
        )
        return {
            "id": t.id,
            "name": t.name,
            "channel": t.channel.value,
            "body": t.body,
            "subject": t.subject,
            "phone_number": t.phone_number,
            "active": t.active,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{template_id}")
def update_template(
    template_id: str, payload: UpdateTemplateRequest, session: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Update template body, subject, attachment, phone number, or active flag."""
    svc = TemplateService(session)
    t = svc.update_template(
        template_id=template_id,
        name=payload.name,
        body=payload.body,
        subject=payload.subject,
        attachment_ref=payload.attachment_ref,
        phone_number=payload.phone_number,
        active=payload.active,
    )
    if not t:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return {
        "id": t.id,
        "name": t.name,
        "channel": t.channel.value,
        "body": t.body,
        "subject": t.subject,
        "phone_number": t.phone_number,
        "active": t.active,
    }
