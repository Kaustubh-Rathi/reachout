"""Outreach history and operator recovery workflows.

Read models for attempt history and the recovery queue, plus the recovery
resolution state machine. Kept separate from dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.enums import OutreachStatus
from app.domain.errors import NotFoundError
from app.ports.infrastructure import Clock
from app.ports.repositories import ContactRepository, OutreachRepository


class OutreachRecoveryService:
    """History, recovery queue, and recovery resolution for outreach attempts."""

    def __init__(
        self,
        session: Session,
        outreach_repo: OutreachRepository,
        contact_repo: ContactRepository,
        clock: Clock,
    ) -> None:
        self.session = session
        self.outreach_repo = outreach_repo
        self.contact_repo = contact_repo
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

    def get_recovery_queue(self) -> List[Dict[str, Any]]:
        """Retrieve all attempts stuck in RECOVERY_REQUIRED or UNKNOWN."""
        combined = self.outreach_repo.list_by_status(OutreachStatus.RECOVERY_REQUIRED)
        combined += self.outreach_repo.list_by_status(OutreachStatus.UNKNOWN)

        results = []
        for a in combined:
            cnt = self.contact_repo.get_by_id(a.contact_id)
            results.append(
                {
                    "id": a.id,
                    "contact_id": a.contact_id,
                    "contact_name": cnt.name if cnt else "Unknown",
                    "company": cnt.company_id if cnt else "Unknown",
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
        attempt.recovery_notes = recovery_notes or f"Resolved via operator action: {action}"

        if action == "mark_sent":
            attempt.status = OutreachStatus.SENT
            attempt.completed_at = now
            cnt = self.contact_repo.get_by_id(attempt.contact_id)
            if cnt:
                cnt.record_outreach_success(attempt.channel, now)
                self.contact_repo.save(cnt)
        elif action == "cancel":
            attempt.status = OutreachStatus.FAILED
            attempt.failure_code = "OPERATOR_CANCELLED"
            attempt.failure_detail = "Operator cancelled in recovery queue"
            attempt.completed_at = now
        elif action == "retry":
            attempt.status = OutreachStatus.PREPARED

        self.outreach_repo.save(attempt)
        self.session.commit()

        return {
            "attempt_id": attempt.id,
            "status": attempt.status.value,
            "recovery_notes": attempt.recovery_notes,
        }
