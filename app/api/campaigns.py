"""Campaigns control plane API endpoints.

Handles campaign creation, starting (with dynamic DB eligibility calculation),
pausing, resuming, stopping, quota configuration, and progress inspection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import DEFAULT_OUTREACH_LIMIT
from app.domain.enums import Channel
from app.infrastructure.database import get_session
from app.services.campaign_service import CampaignService

router = APIRouter(prefix="/api/campaigns", tags=["Campaigns"])


class CampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=255)
    channel: str = Field(..., description="'WHATSAPP' or 'EMAIL'")
    template_ids: Optional[List[str]] = Field(default_factory=list)
    sender_account_ids: Optional[List[str]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class QuickStartRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel: Optional[str] = Field("WHATSAPP", description="'WHATSAPP' or 'EMAIL'")
    name: Optional[str] = Field(None)
    max_count: Optional[int] = Field(default=DEFAULT_OUTREACH_LIMIT, ge=1)


@router.get("")
def list_campaigns(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """List all campaigns with progress metrics."""
    svc = CampaignService(session)
    return svc.list_campaigns()


@router.post("")
def create_campaign(payload: CampaignCreateRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Create a new campaign entity."""
    svc = CampaignService(session)
    try:
        ch = Channel(payload.channel.upper())
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid channel '{payload.channel}'. Must be 'WHATSAPP' or 'EMAIL'."
        ) from None

    campaign = svc.create_campaign(
        name=payload.name,
        channel=ch,
        template_ids=payload.template_ids,
        sender_account_ids=payload.sender_account_ids,
        metadata=payload.metadata,
    )
    return svc.get_campaign_progress(campaign.id)


@router.get("/readiness")
def check_outreach_readiness(
    channel: Optional[str] = Query("WHATSAPP", description="Target outreach channel"),
    campaign_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Pre-flight validation check verifying all prerequisites before starting outreach."""
    svc = CampaignService(session)
    try:
        ch = Channel(channel.upper() if channel else "WHATSAPP")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid channel '{channel}'") from None
    return svc.validate_outreach_readiness(channel=ch, campaign_id=campaign_id)


@router.post("/quick-start")
def quick_start_campaign(
    payload: QuickStartRequest = QuickStartRequest(), session: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Convenience endpoint to launch an outreach campaign immediately on eligible DB contacts."""
    svc = CampaignService(session)
    channel_str = payload.channel or "WHATSAPP"
    try:
        ch = Channel(channel_str.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid channel '{channel_str}'") from None

    # Upfront readiness check
    readiness = svc.validate_outreach_readiness(channel=ch)
    if not readiness["ready"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "OUTREACH_NOT_READY",
                "reason": readiness["reason"],
                "message": readiness["detail"],
            },
        )

    name = payload.name or f"{ch.value.title()} Outreach Run"
    campaign = svc.create_campaign(
        name=name,
        channel=ch,
    )
    try:
        return svc.start_campaign(campaign.id, max_count=payload.max_count)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "OUTREACH_NOT_READY",
                "reason": "VALIDATION_FAILED",
                "message": str(exc),
            },
        ) from exc


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Get campaign details and current progress."""
    svc = CampaignService(session)
    try:
        return svc.get_campaign_progress(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{campaign_id}/start")
def start_campaign(
    campaign_id: str, max_count: Optional[int] = Query(None), session: Session = Depends(get_session)
) -> Dict[str, Any]:
    """Start campaign. Computes eligible contacts dynamically from current DB state."""
    svc = CampaignService(session)
    try:
        return svc.start_campaign(campaign_id, max_count=max_count)
    except ValueError as exc:
        msg = str(exc)
        if "OUTREACH_NOT_READY" in msg:
            parts = msg.split(":", 1)
            reason_detail = parts[1].strip() if len(parts) > 1 else msg
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "OUTREACH_NOT_READY",
                    "reason": reason_detail.split(" - ")[0] if " - " in reason_detail else "VALIDATION_FAILED",
                    "message": reason_detail,
                },
            ) from exc
        raise HTTPException(status_code=400, detail=msg) from exc


@router.post("/{campaign_id}/pause")
def pause_campaign(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Pause an active campaign."""
    svc = CampaignService(session)
    try:
        return svc.pause_campaign(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{campaign_id}/resume")
def resume_campaign(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Resume a paused campaign with readiness check."""
    svc = CampaignService(session)
    try:
        return svc.resume_campaign(campaign_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from exc
        if "OUTREACH_NOT_READY" in msg:
            parts = msg.split(":", 1)
            reason_detail = parts[1].strip() if len(parts) > 1 else msg
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "OUTREACH_NOT_READY",
                    "reason": reason_detail.split(" - ")[0] if " - " in reason_detail else "VALIDATION_FAILED",
                    "message": reason_detail,
                },
            ) from exc
        raise HTTPException(status_code=400, detail=msg) from exc


@router.post("/{campaign_id}/stop")
def stop_campaign(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Stop/cancel a campaign."""
    svc = CampaignService(session)
    try:
        return svc.stop_campaign(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{campaign_id}/progress")
def get_campaign_progress(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Inspect real-time progress metrics for a campaign."""
    svc = CampaignService(session)
    try:
        return svc.get_campaign_progress(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{campaign_id}/status")
def get_campaign_status(campaign_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Alias for campaign progress / status inspection."""
    svc = CampaignService(session)
    try:
        return svc.get_campaign_progress(campaign_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
