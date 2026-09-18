"""Senders API endpoints.

Handles multi-account sender inventory and operational status management.
Never exposes credentials, passwords, tokens, or session secrets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.domain.enums import Channel, SenderStatus
from app.services.sender_service import SenderService

router = APIRouter(prefix="/api/senders", tags=["Senders"])


class UpdateSenderStatusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., description="'ACTIVE', 'AUTH_REQUIRED', 'TOKEN_EXPIRED', or 'SUSPENDED'")


class CreateSenderRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    channel: str = Field(..., description="'WHATSAPP' or 'EMAIL'")
    provider: str = Field(..., min_length=1)
    identity: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    status: Optional[str] = "ACTIVE"
    daily_limit: Optional[int] = 50
    hourly_limit: Optional[int] = 10


class ConfigureWhatsAppRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    count: int = Field(..., ge=1, le=100, description="Number of WhatsApp sessions to configure")


class AddSenderRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    display_name: Optional[str] = None
    identity: Optional[str] = None


class ConfigureEmailRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    identity: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    host: Optional[str] = None
    port: Optional[int] = 587
    user: Optional[str] = None
    password: Optional[str] = None
    verify_now: Optional[bool] = True


@router.get("/readiness")
def get_senders_readiness(session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Retrieve multi-channel sender readiness and active session counts."""
    svc = SenderService(session)
    return svc.get_senders_readiness()


@router.post("/whatsapp/add")
def add_whatsapp_session(
    payload: AddSenderRequest = AddSenderRequest(),
    session: Session = Depends(get_db_session),
) -> Dict[str, Any]:
    """Dynamically add a new independent WhatsApp session in AUTH_REQUIRED status."""
    svc = SenderService(session)
    return svc.add_whatsapp_session(display_name=payload.display_name, identity=payload.identity)


@router.post("/whatsapp/configure")
def configure_whatsapp_sessions(
    payload: ConfigureWhatsAppRequest, session: Session = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    """Configure N independent WhatsApp sessions."""
    svc = SenderService(session)
    try:
        return svc.configure_whatsapp_sessions(payload.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/whatsapp/{sender_id}/auth/start")
def start_whatsapp_auth(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Start QR authentication process for a WhatsApp session."""
    svc = SenderService(session)
    try:
        return svc.start_whatsapp_authentication(sender_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/whatsapp/{sender_id}/auth/status")
def get_whatsapp_auth_status(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Get live authentication status and QR code if required."""
    svc = SenderService(session)
    try:
        return svc.get_whatsapp_auth_status(sender_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/whatsapp/{sender_id}/auth/check")
def check_whatsapp_session_health(
    sender_id: str, timeout_seconds: int = Query(20, ge=5, le=120), session: Session = Depends(get_db_session)
) -> Dict[str, Any]:
    """Probe the sender's browser profile and sync the stored status.

    Opens the persisted profile in a live browser (may take up to
    ``timeout_seconds``). Downgrades the sender on positive evidence of a dead
    login; inconclusive probes are reported without changing stored status.
    """
    svc = SenderService(session)
    try:
        return svc.check_whatsapp_session_health(sender_id, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/email/configure")
def configure_email_sender(
    payload: ConfigureEmailRequest, session: Session = Depends(get_db_session)
) -> Dict[str, Any]:
    """Configure or register an Email sender identity and optionally test credentials."""
    svc = SenderService(session)
    try:
        return svc.configure_email_sender(
            id=payload.id,
            identity=payload.identity,
            display_name=payload.display_name,
            host=payload.host,
            port=payload.port,
            user=payload.user,
            password=payload.password,
            verify_now=payload.verify_now if payload.verify_now is not None else True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/email/{sender_id}/verify")
def verify_email_sender(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Verify SMTP credentials for an Email sender identity."""
    svc = SenderService(session)
    try:
        return svc.verify_email_sender(sender_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_senders(
    channel: Optional[str] = Query(None, description="Optional channel filter: WHATSAPP or EMAIL"),
    session: Session = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List all sender accounts with operational status. Zero credential exposure."""
    svc = SenderService(session)
    return svc.list_senders(channel=channel)


@router.get("/{sender_id}")
def get_sender(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Retrieve sender account info."""
    svc = SenderService(session)
    sender = svc.get_sender(sender_id)
    if not sender:
        raise HTTPException(status_code=404, detail=f"Sender account '{sender_id}' not found")
    return {
        "id": sender.id,
        "channel": sender.channel.value,
        "provider": sender.provider,
        "identity": sender.identity,
        "display_name": sender.display_name,
        "status": sender.status.value,
        "last_used_at": sender.last_used_at.isoformat() if sender.last_used_at else None,
        "daily_limit": sender.daily_limit,
        "hourly_limit": sender.hourly_limit,
    }


@router.put("/{sender_id}/status")
def update_sender_status(
    sender_id: str, payload: UpdateSenderStatusRequest, session: Session = Depends(get_db_session)
) -> Dict[str, Any]:
    """Update sender operational status."""
    svc = SenderService(session)
    try:
        updated = svc.update_sender_status(sender_id, payload.status)
        if not updated:
            raise HTTPException(status_code=404, detail=f"Sender '{sender_id}' not found")
        return updated
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_sender(payload: CreateSenderRequest, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Register a new sender identity."""
    svc = SenderService(session)
    try:
        ch = Channel(payload.channel.upper())
        st = SenderStatus(payload.status.upper()) if payload.status else SenderStatus.ACTIVE
        return svc.create_sender(
            id=payload.id,
            channel=ch,
            provider=payload.provider,
            identity=payload.identity,
            display_name=payload.display_name,
            status=st,
            daily_limit=payload.daily_limit,
            hourly_limit=payload.hourly_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{sender_id}/deactivate")
def deactivate_sender(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Deactivate a sender account so it exits future rotation without deleting history."""
    svc = SenderService(session)
    res = svc.deactivate_sender(sender_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Sender account '{sender_id}' not found")
    return res


@router.post("/{sender_id}/reactivate")
def reactivate_sender(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Reactivate a deactivated sender account."""
    svc = SenderService(session)
    res = svc.reactivate_sender(sender_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Sender account '{sender_id}' not found")
    return res


@router.delete("/{sender_id}")
def delete_sender(sender_id: str, session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """Controlled deletion/deactivation of sender (preserves historical attempts)."""
    svc = SenderService(session)
    success = svc.remove_sender(sender_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Sender account '{sender_id}' not found")
    return {"message": f"Sender '{sender_id}' deactivated and removed from active rotation", "sender_id": sender_id}
