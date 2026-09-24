"""Outreach history and operator recovery workflows.

Read models for attempt history and the recovery queue, plus the recovery
resolution state machine. Kept separate from dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.endpoint import normalize_email_addresses, normalize_phone_numbers
from app.domain.enums import AttemptType, Channel, OutreachStatus
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.policies.endpoint_coverage_policy import DEFINITIVE_ENDPOINT_FAILURES
from app.ports.infrastructure import Clock
from app.ports.repositories import CampaignRepository, CompanyRepository, ContactRepository, OutreachRepository


class OutreachRecoveryService:
    """History, recovery queue, and recovery resolution for outreach attempts."""

    def __init__(
        self,
        session: Session,
        outreach_repo: OutreachRepository,
        contact_repo: ContactRepository,
        company_repo: CompanyRepository,
        campaign_repo: CampaignRepository,
        clock: Clock,
    ) -> None:
        self.session = session
        self.outreach_repo = outreach_repo
        self.contact_repo = contact_repo
        self.company_repo = company_repo
        self.campaign_repo = campaign_repo
        self.clock = clock

    def get_history(self, contact_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete historical attempts for a contact."""
        return [
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
                "attachment": a.attachment_snapshot,
                "prepared_at": a.prepared_at.isoformat() if a.prepared_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "failure_code": a.failure_code,
                "failure_detail": a.failure_detail,
                "provider_reference": a.provider_reference,
                "recovery_notes": a.recovery_notes,
            }
            for a in self.outreach_repo.list_by_contact(contact_id)
        ]

    def get_failed_attempts(
        self,
        campaign_id: Optional[str] = None,
        channel: Optional[Channel] = None,
        failure_code: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: Optional[int] = 50,
        stop_after: Optional[int] = None,
        failure_detail_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        companies = {company.id: company.name for company in self.company_repo.list_all()}
        search_text = (search or "").strip().lower()
        page = []
        total = 0
        failure_code_facets = set()
        destinations = set()

        for attempt in self.outreach_repo.iter_by_status(
            OutreachStatus.FAILED,
            failure_detail_limit=failure_detail_limit,
        ):
            if campaign_id and attempt.campaign_id != campaign_id:
                continue
            if channel and attempt.channel != channel:
                continue

            contact = self.contact_repo.get_by_id(attempt.contact_id)
            contact_name = contact.name if contact else "Unknown"
            company_id = contact.company_id if contact else "Unknown"
            company_name = companies.get(company_id, company_id)
            if search_text:
                searchable = " ".join(
                    [contact_name, company_name, attempt.destination or "", attempt.failure_code or ""]
                ).lower()
                if search_text not in searchable:
                    continue

            if attempt.failure_code:
                failure_code_facets.add(attempt.failure_code)
            if failure_code and attempt.failure_code != failure_code:
                continue

            normalized_destination = self._normalize_destination(attempt.channel, attempt.destination)
            if normalized_destination:
                destinations.add((attempt.channel.value, normalized_destination))
            matched_index = total
            total += 1
            if offset <= matched_index and (limit is None or matched_index < offset + limit):
                page.append(
                    {
                        "attempt_id": attempt.id,
                        "contact_id": attempt.contact_id,
                        "contact_name": contact_name,
                        "company_id": company_id,
                        "company_name": company_name,
                        "campaign_id": attempt.campaign_id,
                        "attempt_type": attempt.attempt_type.value,
                        "channel": attempt.channel.value,
                        "destination": attempt.destination,
                        "normalized_destination": normalized_destination,
                        "sender_account_id": attempt.sender_account_id,
                        "template_id": attempt.template_id,
                        "status": attempt.status.value,
                        "failure_code": attempt.failure_code,
                        "failure_detail": attempt.failure_detail,
                        "failure_class": "PERMANENT"
                        if attempt.failure_code in DEFINITIVE_ENDPOINT_FAILURES
                        else "RETRYABLE",
                        "prepared_at": attempt.prepared_at.isoformat() if attempt.prepared_at else None,
                        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
                        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
                    }
                )
            if stop_after is not None and total >= stop_after:
                break

        return {
            "items": page,
            "total": total,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "has_more": limit is not None and offset + len(page) < total,
            "failed_destinations": len(destinations),
            "failure_codes": sorted(failure_code_facets),
        }

    @staticmethod
    def _normalize_destination(channel: Channel, destination: Optional[str]) -> Optional[str]:
        if not destination:
            return None
        if channel == Channel.WHATSAPP:
            values = normalize_phone_numbers(destination)
            return values[0] if values else destination.strip()
        values = normalize_email_addresses(destination)
        return values[0] if values else destination.strip().lower()

    def get_recovery_queue(self) -> List[Dict[str, Any]]:
        """Retrieve all attempts stuck in RECOVERY_REQUIRED or UNKNOWN."""
        combined = self.outreach_repo.list_by_status(OutreachStatus.RECOVERY_REQUIRED)
        combined += self.outreach_repo.list_by_status(OutreachStatus.UNKNOWN)

        results = []
        companies = {company.id: company.name for company in self.company_repo.list_all()}
        for a in combined:
            cnt = self.contact_repo.get_by_id(a.contact_id)
            company_id = cnt.company_id if cnt else "Unknown"
            results.append(
                {
                    "id": a.id,
                    "contact_id": a.contact_id,
                    "contact_name": cnt.name if cnt else "Unknown",
                    "company": companies.get(company_id, company_id),
                    "channel": a.channel.value,
                    "destination": a.destination,
                    "status": a.status.value,
                    "failure_code": a.failure_code,
                    "failure_detail": a.failure_detail,
                    "prepared_at": a.prepared_at.isoformat() if a.prepared_at else None,
                    "recovery_notes": a.recovery_notes,
                }
            )
        return results

    def resolve_recovery(self, attempt_id: str, action: str, recovery_notes: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a stuck recovery attempt: 'mark_sent', 'retry', or 'cancel'."""
        attempt = self.outreach_repo.get_by_id(attempt_id)
        if not attempt:
            raise NotFoundError(f"Attempt not found: {attempt_id}")

        now = self.clock.now()
        if action not in {"mark_sent", "retry", "cancel"}:
            raise ValidationError(f"Unsupported recovery action: {action}")
        if attempt.status not in {OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED}:
            raise ConflictError(f"Attempt {attempt_id} is no longer awaiting recovery")
        notes = recovery_notes or f"Resolved via operator action: {action}"

        if action == "mark_sent":
            resolved = self.outreach_repo.mark_recovery_sent_if_unresolved(attempt.id, now, notes)
            if resolved is None:
                raise ConflictError(f"Attempt {attempt_id} was already resolved by another operator")
            attempt = resolved
            cnt = self.contact_repo.get_by_id(attempt.contact_id)
            if cnt:
                cnt.record_outreach_success(attempt.channel, now)
                self.contact_repo.save(cnt)
            if attempt.attempt_type == AttemptType.AUTOMATIC and attempt.campaign_id:
                self.campaign_repo.increment_automatic_used(attempt.campaign_id)
        else:
            failure_code = "OPERATOR_CANCELLED" if action == "cancel" else "OPERATOR_RETRY_REQUESTED"
            failure_detail = (
                "Operator cancelled in recovery queue"
                if action == "cancel"
                else "Operator requested a fresh dispatch attempt"
            )
            resolved = self.outreach_repo.fail_recovery_if_unresolved(
                attempt.id,
                now,
                notes,
                failure_code,
                failure_detail,
            )
            if resolved is None:
                raise ConflictError(f"Attempt {attempt_id} was already resolved by another operator")
            attempt = resolved

        self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "recovery_notes": attempt.recovery_notes,
        }
