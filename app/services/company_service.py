"""Company application service.

Handles company directory listings and detail queries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.domain.company import Company, calculate_company_status
from app.domain.enums import CompanyStatus
from app.domain.policies.endpoint_coverage_policy import get_contact_endpoint_metrics
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository


class CompanyService:
    """Application service for Company entities, hierarchy inspection, and status management."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.company_repo = SqliteCompanyRepository(session)
        self.contact_repo = SqliteContactRepository(session)
        self.outreach_repo = SqliteOutreachRepository(session)

    def list_companies(self) -> List[Dict[str, Any]]:
        companies = self.company_repo.list_all()
        all_attempts = self.outreach_repo.list_all()
        results = []
        for comp in companies:
            contacts = self.contact_repo.find_by_company(comp.id)
            comp_attempts = [a for a in all_attempts if a.contact_id in {c.contact_id for c in contacts}]
            status = calculate_company_status(contacts, comp_attempts)

            total_endpoints = sum(len(c.endpoints) for c in contacts)
            covered_endpoints = sum(
                get_contact_endpoint_metrics(c, comp_attempts)["covered_endpoints"]
                for c in contacts
            )

            results.append({
                "id": comp.id,
                "name": comp.name,
                "domain": comp.domain,
                "contact_count": len(contacts),
                "total_endpoints": total_endpoints,
                "covered_endpoints": covered_endpoints,
                "status": status.value,
                "is_fully_covered": total_endpoints > 0 and covered_endpoints >= total_endpoints,
                "created_at": comp.created_at.isoformat(),
            })
        return results

    def get_company(self, company_id: str) -> Optional[Dict[str, Any]]:
        comp = self.company_repo.get_by_id(company_id)
        if not comp:
            return None
        contacts = self.contact_repo.find_by_company(comp.id)
        all_attempts = self.outreach_repo.list_all()
        comp_attempts = [a for a in all_attempts if a.contact_id in {c.contact_id for c in contacts}]
        status = calculate_company_status(contacts, comp_attempts)

        return {
            "id": comp.id,
            "name": comp.name,
            "domain": comp.domain,
            "status": status.value,
            "contact_count": len(contacts),
            "created_at": comp.created_at.isoformat(),
            "contacts": [
                {
                    "contact_id": c.contact_id,
                    "name": c.name,
                    "phone": c.phone,
                    "email": c.email,
                    "crm_outcome": c.crm_outcome.value,
                }
                for c in contacts
            ],
        }

    def get_company_hierarchy(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full hierarchy for a company: Company -> HR Contacts -> Endpoints -> Attempts."""
        comp = self.company_repo.get_by_id(company_id)
        if not comp:
            return None

        contacts = self.contact_repo.find_by_company(comp.id)
        all_attempts = self.outreach_repo.list_all()
        comp_attempts = [a for a in all_attempts if a.contact_id in {c.contact_id for c in contacts}]
        status = calculate_company_status(contacts, comp_attempts)

        # Attempts lookup by contact
        attempts_by_contact: Dict[str, List] = {}
        for a in comp_attempts:
            if a.contact_id not in attempts_by_contact:
                attempts_by_contact[a.contact_id] = []
            attempts_by_contact[a.contact_id].append(a)

        contacts_hierarchy = []
        total_endpoints = 0
        covered_endpoints = 0

        for c in contacts:
            c_attempts = attempts_by_contact.get(c.contact_id, [])
            coverage = get_contact_endpoint_metrics(c, c_attempts)
            total_endpoints += coverage["total_endpoints"]
            covered_endpoints += coverage["covered_endpoints"]

            endpoints_list = []
            # WhatsApp endpoints
            for ep in coverage["whatsapp_endpoints"]:
                endpoints_list.append({
                    "channel": "WHATSAPP",
                    "address": ep["address"],
                    "normalized_address": ep["normalized_address"],
                    "ordinal": ep["ordinal"],
                    "label": f"Phone {ep['ordinal'] + 1}",
                    "status": ep["status"],
                    "is_covered": ep["is_covered"],
                    "sender_account_id": ep["sender_account_id"],
                    "template_id": ep["template_id"],
                    "attempt_id": ep["attempt_id"],
                    "sent_at": ep["sent_at"],
                })

            # Email endpoints
            for ep in coverage["email_endpoints"]:
                endpoints_list.append({
                    "channel": "EMAIL",
                    "address": ep["address"],
                    "normalized_address": ep["normalized_address"],
                    "ordinal": ep["ordinal"],
                    "label": f"Email {ep['ordinal'] + 1}",
                    "status": ep["status"],
                    "is_covered": ep["is_covered"],
                    "sender_account_id": ep["sender_account_id"],
                    "template_id": ep["template_id"],
                    "attempt_id": ep["attempt_id"],
                    "sent_at": ep["sent_at"],
                })

            contacts_hierarchy.append({
                "contact_id": c.contact_id,
                "name": c.name,
                "first_name": c.first_name,
                "designation": c.designation or "",
                "phone": c.phone or "",
                "email": c.email or "",
                "crm_outcome": c.crm_outcome.value,
                "interview_status": c.interview_status.value,
                "notes": c.notes or "",
                "tags": c.tags or [],
                "last_whatsapp_at": c.last_whatsapp_at.isoformat() if c.last_whatsapp_at else None,
                "last_email_at": c.last_email_at.isoformat() if c.last_email_at else None,
                "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
                "coverage": coverage,
                "is_fully_covered": coverage["is_fully_covered"],
                "endpoints": endpoints_list,
            })

        return {
            "id": comp.id,
            "name": comp.name,
            "domain": comp.domain,
            "status": status.value,
            "total_contacts": len(contacts),
            "total_endpoints": total_endpoints,
            "covered_endpoints": covered_endpoints,
            "is_fully_covered": total_endpoints > 0 and covered_endpoints >= total_endpoints,
            "created_at": comp.created_at.isoformat(),
            "contacts": contacts_hierarchy,
        }

    def list_hierarchies(self, search: Optional[str] = None, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all company hierarchies with filtering."""
        companies = self.company_repo.list_all()
        results = []
        for comp in companies:
            h = self.get_company_hierarchy(comp.id)
            if not h:
                continue

            if search:
                s = search.lower().strip()
                matches_comp = s in h["name"].lower() or (h["domain"] and s in h["domain"].lower())
                matches_contacts = any(
                    s in c["name"].lower() or s in c["phone"].lower() or s in c["email"].lower()
                    for c in h["contacts"]
                )
                if not matches_comp and not matches_contacts:
                    continue

            if status_filter and status_filter != "ALL":
                if h["status"].upper() != status_filter.upper():
                    continue

            results.append(h)
        return results
