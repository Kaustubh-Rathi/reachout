"""Contact domain & query application service.

Implements search, filtering, deterministic priority sorting, detail views,
and tombstone archival.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.contact import Contact
from app.domain.enums import CRMOutcome
from app.domain.policies.endpoint_coverage_policy import get_contact_endpoint_metrics
from app.domain.policies.reminder_policy import DEFAULT_FOLLOW_UP_THRESHOLD_DAYS, check_contact_follow_up_eligibility
from app.services.context import ServiceContext, build_service_context


class ContactService:
    """Application service for contact listings, detail inspection, and lifecycle management."""

    def __init__(self, session: Session, context: Optional[ServiceContext] = None) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.contact_repo = ctx.contact_repo
        self.company_repo = ctx.company_repo
        self.outreach_repo = ctx.outreach_repo
        self.reminder_repo = ctx.reminder_repo
        self.suppression_repo = ctx.suppression_repo
        self.event_publisher = ctx.event_publisher

    def list_contacts(
        self,
        search: Optional[str] = None,
        company: Optional[str] = None,
        crm_status: Optional[str] = None,
        send_status: Optional[str] = None,
        priority_filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """List contacts with full priority sorting, endpoint coverage metrics, and filtering."""
        now = current_time or datetime.now(timezone.utc)
        all_contacts = self.contact_repo.list_all()
        all_attempts = self.outreach_repo.list_all()

        # Build attempts map by contact
        attempts_by_contact: Dict[str, List] = {}
        for att in all_attempts:
            if att.contact_id not in attempts_by_contact:
                attempts_by_contact[att.contact_id] = []
            attempts_by_contact[att.contact_id].append(att)

        # Build companies lookup for clean company names
        companies = self.company_repo.list_all()
        comp_map = {c.id: c.name for c in companies}

        items: List[Dict[str, Any]] = []

        for c in all_contacts:
            comp_name = comp_map.get(c.company_id, c.company_id.title() if c.company_id else "Unknown")
            c_attempts = attempts_by_contact.get(c.contact_id, [])
            coverage = get_contact_endpoint_metrics(c, c_attempts)

            # Determine channel statuses
            wa_status = "SENT" if (c.last_whatsapp_at or coverage["whatsapp_covered"] > 0) else "NOT_SENT"
            email_status = "SENT" if (c.last_email_at or coverage["email_covered"] > 0) else "NOT_SENT"

            # Check follow-up due status
            follow_up = check_contact_follow_up_eligibility(c, now, DEFAULT_FOLLOW_UP_THRESHOLD_DAYS)

            last_contacted = None
            if c.last_whatsapp_at and c.last_email_at:
                last_contacted = max(c.last_whatsapp_at, c.last_email_at)
            elif c.last_whatsapp_at:
                last_contacted = c.last_whatsapp_at
            elif c.last_email_at:
                last_contacted = c.last_email_at

            # Calculate priority rank according to prompt section 8:
            # 1. Interested contacts
            # 2. Recently active/contacted
            # 3. Pending follow-up
            # 4. Uncontacted
            # 5. Not interested
            if c.crm_outcome == CRMOutcome.NOT_INTERESTED:
                priority_rank = 5
            elif c.crm_outcome == CRMOutcome.INTERESTED:
                priority_rank = 1
            elif follow_up.is_due:
                priority_rank = 3
            elif c.last_activity_at or last_contacted:
                priority_rank = 2
            else:
                priority_rank = 4

            # Formulate sort timestamp (newest first within tier)
            sort_ts = (
                c.interested_at
                or c.last_activity_at
                or last_contacted
                or c.updated_at
                or c.created_at
                or datetime.fromtimestamp(0, tz=timezone.utc)
            )

            src_file = c.source_reference.source_file if c.source_reference else ""
            src_row = c.source_reference.source_row if c.source_reference else 0

            item = {
                "contact_id": c.contact_id,
                "company_id": c.company_id,
                "company": comp_name,
                "name": c.name,
                "first_name": c.first_name,
                "designation": c.designation or "",
                "phone": c.phone or "",
                "email": c.email or "",
                "phones": c.phones if hasattr(c, "phones") else [],
                "emails": c.emails if hasattr(c, "emails") else [],
                "coverage": coverage,
                "is_fully_covered": coverage["is_fully_covered"],
                "total_endpoints": coverage["total_endpoints"],
                "covered_endpoints": coverage["covered_endpoints"],
                "whatsapp_status": wa_status,
                "email_status": email_status,
                "last_whatsapp_at": c.last_whatsapp_at.isoformat() if c.last_whatsapp_at else None,
                "last_email_at": c.last_email_at.isoformat() if c.last_email_at else None,
                "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
                "last_contacted": last_contacted.isoformat() if last_contacted else None,
                "crm_outcome": c.crm_outcome.value,
                "interview_status": c.interview_status.value,
                "interested_at": c.interested_at.isoformat() if c.interested_at else None,
                "follow_up_due": follow_up.is_due,
                "follow_up_due_at": follow_up.due_at.isoformat() if follow_up.due_at else None,
                "follow_up_reason": follow_up.reason,
                "notes": c.notes or "",
                "tags": c.tags or [],
                "source_file": src_file,
                "source_row": src_row,
                "canonical_key": c.canonical_key,
                "priority_rank": priority_rank,
                "_sort_ts": sort_ts,
            }
            items.append(item)

        # Filtering
        if search:
            s = search.lower().strip()
            items = [
                x
                for x in items
                if s in x["name"].lower()
                or s in x["company"].lower()
                or s in x["phone"].lower()
                or s in x["email"].lower()
                or s in x["notes"].lower()
                or s in x["designation"].lower()
            ]

        if company and company != "ALL":
            items = [
                x
                for x in items
                if x["company"].lower() == company.lower() or x["company_id"].lower() == company.lower()
            ]

        if crm_status and crm_status != "ALL":
            items = [x for x in items if x["crm_outcome"].lower() == crm_status.lower()]

        if send_status and send_status != "ALL":
            if send_status.upper() == "SENT":
                items = [x for x in items if x["whatsapp_status"] == "SENT" or x["email_status"] == "SENT"]
            elif send_status.upper() == "NOT_SENT":
                items = [x for x in items if x["whatsapp_status"] == "NOT_SENT" and x["email_status"] == "NOT_SENT"]
            elif send_status.upper() == "WHATSAPP_SENT":
                items = [x for x in items if x["whatsapp_status"] == "SENT"]
            elif send_status.upper() == "EMAIL_SENT":
                items = [x for x in items if x["email_status"] == "SENT"]

        if priority_filter and priority_filter != "ALL":
            pf = priority_filter.upper()
            if pf == "INTERESTED":
                items = [x for x in items if x["crm_outcome"] == "INTERESTED"]
            elif pf == "FOLLOW_UP_DUE":
                items = [x for x in items if x["follow_up_due"]]
            elif pf == "RECENTLY_ACTIVE":
                items = [x for x in items if x["last_activity_at"] or x["last_contacted"]]
            elif pf == "UNCONTACTED":
                items = [
                    x
                    for x in items
                    if x["whatsapp_status"] == "NOT_SENT"
                    and x["email_status"] == "NOT_SENT"
                    and x["crm_outcome"] != "NOT_INTERESTED"
                ]
            elif pf == "NOT_INTERESTED":
                items = [x for x in items if x["crm_outcome"] == "NOT_INTERESTED"]

        # Sorting: priority_rank ascending (1 to 5), then sort_ts descending (newest first), then source_row ascending
        items.sort(key=lambda x: (x["priority_rank"], -(x["_sort_ts"].timestamp()), x["source_row"]))

        total_count = len(items)

        # Pagination if requested
        if limit is not None and limit > 0:
            paged_items = items[offset : offset + limit]
        else:
            paged_items = items

        # Clean internal sort helper
        for p in paged_items:
            p.pop("_sort_ts", None)

        return {
            "total": total_count,
            "count": len(paged_items),
            "contacts": paged_items,
        }

    def get_contact_detail(self, contact_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve rich contact detail including attempt history and active reminders."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            return None

        comp = self.company_repo.get_by_id(contact.company_id)
        attempts = self.outreach_repo.list_by_contact(contact_id)
        reminders = self.reminder_repo.list_by_contact(contact_id)

        history = [
            {
                "id": a.id,
                "channel": a.channel.value,
                "attempt_type": a.attempt_type.value,
                "status": a.status.value,
                "destination": a.destination,
                "sender_account_id": a.sender_account_id,
                "template_id": a.template_id,
                "subject": a.subject_snapshot,
                "message_body": a.message_body_snapshot,
                "prepared_at": a.prepared_at.isoformat() if a.prepared_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "failure_code": a.failure_code,
                "failure_detail": a.failure_detail,
                "provider_reference": a.provider_reference,
            }
            for a in attempts
        ]

        rem_list = [
            {
                "id": r.id,
                "due_at": r.due_at.isoformat(),
                "reason": r.reason,
                "status": r.status.value,
                "created_at": r.created_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in reminders
        ]

        coverage = get_contact_endpoint_metrics(contact, attempts)

        return {
            "contact_id": contact.contact_id,
            "company_id": contact.company_id,
            "company_name": comp.name if comp else contact.company_id.title(),
            "name": contact.name,
            "first_name": contact.first_name,
            "designation": contact.designation,
            "phone": contact.phone,
            "email": contact.email,
            "phones": contact.phones if hasattr(contact, "phones") else [],
            "emails": contact.emails if hasattr(contact, "emails") else [],
            "coverage": coverage,
            "is_fully_covered": coverage["is_fully_covered"],
            "total_endpoints": coverage["total_endpoints"],
            "covered_endpoints": coverage["covered_endpoints"],
            "crm_outcome": contact.crm_outcome.value,
            "interview_status": contact.interview_status.value,
            "interested_at": contact.interested_at.isoformat() if contact.interested_at else None,
            "notes": contact.notes,
            "tags": contact.tags,
            "created_at": contact.created_at.isoformat(),
            "updated_at": contact.updated_at.isoformat(),
            "last_whatsapp_at": contact.last_whatsapp_at.isoformat() if contact.last_whatsapp_at else None,
            "last_email_at": contact.last_email_at.isoformat() if contact.last_email_at else None,
            "last_activity_at": contact.last_activity_at.isoformat() if contact.last_activity_at else None,
            "history": history,
            "reminders": rem_list,
        }

    def get_discrepancies(self) -> Dict[str, Any]:
        """Detect data discrepancies: the same phone or email owned by multiple contacts
        (typically the same person appearing under different companies / rows). These are
        surfaced in the UI for review/merge rather than being silently dropped."""
        contacts = self.contact_repo.list_all()
        by_phone: Dict[str, List[Contact]] = {}
        by_email: Dict[str, List[Contact]] = {}
        for c in contacts:
            for p in c.phones:
                by_phone.setdefault(p, []).append(c)
            for e in c.emails:
                by_email.setdefault(e, []).append(c)

        groups = []
        seen = set()
        for phone, owners in by_phone.items():
            if len(owners) > 1:
                cids = tuple(sorted(o.contact_id for o in owners))
                if cids not in seen:
                    seen.add(cids)
                    groups.append(self._make_group("PHONE", phone, owners))
        for email, owners in by_email.items():
            if len(owners) > 1:
                cids = tuple(sorted(o.contact_id for o in owners))
                if cids not in seen:
                    seen.add(cids)
                    groups.append(self._make_group("EMAIL", email, owners))

        return {"count": len(groups), "groups": groups}

    def _make_group(self, kind: str, identifier: str, owners: List[Contact]) -> Dict[str, Any]:
        comp_map = {c.id: c.name for c in self.company_repo.list_all()}
        return {
            "kind": kind,
            "identifier": identifier,
            "contacts": [
                {
                    "contact_id": c.contact_id,
                    "company_id": c.company_id,
                    "company": comp_map.get(c.company_id, c.company_id.title() if c.company_id else "Unknown"),
                    "name": c.name,
                    "phone": c.phone or "",
                    "email": c.email or "",
                    "last_whatsapp_at": c.last_whatsapp_at.isoformat() if c.last_whatsapp_at else None,
                }
                for c in owners
            ],
        }

    def archive_contact(self, contact_id: str, reason: str = "MANUAL_CRM_DELETION") -> bool:
        """Delete contact and record tombstone suppressions for phone, email, and canonical key."""
        contact = self.contact_repo.get_by_id(contact_id)
        if not contact:
            return False

        # Add tombstone suppression records so source synchronizer won't resurrect it
        if contact.phone:
            self.suppression_repo.add_suppression("PHONE", contact.phone, reason)
        if contact.email:
            self.suppression_repo.add_suppression("EMAIL", contact.email, reason)
        self.suppression_repo.add_suppression("CANONICAL_KEY", contact.canonical_key, reason)

        # Delete through the repository abstraction
        self.contact_repo.delete(contact.contact_id)
        self.session.commit()

        self.event_publisher.publish_event(
            "CONTACT_ARCHIVED",
            {
                "contact_id": contact_id,
                "name": contact.name,
                "company": contact.company_id,
                "reason": reason,
            },
        )
        return True
