"""Companies API endpoints.

Handles company directory listings and company detail queries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.infrastructure.database import get_session
from app.services.company_service import CompanyService

router = APIRouter(prefix="/api/companies", tags=["Companies"])


@router.get("")
def list_companies(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """List all registered companies with status and contact counts."""
    svc = CompanyService(session)
    return svc.list_companies()


@router.get("/hierarchy")
def list_company_hierarchies(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Company status: NOT_CONTACTED/IN_PROGRESS/CONTACTED/CLOSED"),
    crm_status: Optional[str] = Query(None, description="CRM outcome to match on any HR contact"),
    priority_filter: Optional[str] = Query(
        None, description="INTERESTED/NOT_INTERESTED/FOLLOW_UP_DUE/RECENTLY_ACTIVE/UNCONTACTED"
    ),
    company: Optional[str] = Query(None, description="Company id or name to filter by"),
    channel_status: Optional[str] = Query(None, description="SENT/NOT_SENT/WHATSAPP_SENT/EMAIL_SENT"),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """List all companies with full HR and endpoint hierarchies."""
    svc = CompanyService(session)
    return svc.list_hierarchies(
        search=search,
        status_filter=status,
        crm_status=crm_status,
        priority_filter=priority_filter,
        company=company,
        channel_status=channel_status,
    )


@router.get("/{company_id}")
def get_company(company_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve company detail with list of associated contacts."""
    svc = CompanyService(session)
    comp = svc.get_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail=f"Company '{company_id}' not found")
    return comp


@router.get("/{company_id}/hierarchy")
def get_company_hierarchy(company_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve full company hierarchy: Company -> HR Contacts -> Endpoints -> Attempts."""
    svc = CompanyService(session)
    h = svc.get_company_hierarchy(company_id)
    if not h:
        raise HTTPException(status_code=404, detail=f"Company '{company_id}' not found")
    return h
