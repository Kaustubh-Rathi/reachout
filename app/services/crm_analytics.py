"""CRM KPI aggregation.

Computes dashboard KPI metrics from contacts and outreach attempts. Kept
separate from CRM command handling (status transitions, reminders, notes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.domain.endpoint import normalize_email_addresses, normalize_phone_numbers
from app.domain.enums import Channel, CRMOutcome, InterviewState, OutreachStatus
from app.domain.policies.endpoint_coverage_policy import get_contact_endpoint_metrics
from app.domain.policies.reminder_policy import (
    DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
    check_contact_follow_up_eligibility,
)
from app.ports.infrastructure import Clock
from app.ports.repositories import ContactRepository, OutreachRepository, SuppressionRepository


class CrmAnalyticsService:
    """Aggregates system-wide CRM KPI metrics."""

    def __init__(
        self,
        contact_repo: ContactRepository,
        outreach_repo: OutreachRepository,
        suppression_repo: SuppressionRepository,
        clock: Clock,
    ) -> None:
        self.contact_repo = contact_repo
        self.outreach_repo = outreach_repo
        self.suppression_repo = suppression_repo
        self.clock = clock

    def get_coverage_snapshot(self) -> Dict[str, Any]:
        contacts = self.contact_repo.list_all()
        attempts = self.outreach_repo.list_all()
        attempts_by_contact: Dict[str, list] = {}
        for attempt in attempts:
            attempts_by_contact.setdefault(attempt.contact_id, []).append(attempt)

        suppressed = {record.identifier for record in self.suppression_repo.list_all()}

        total_contacts = len(contacts)
        contacts_without_valid_endpoints = 0
        contacts_with_any_send = 0
        fully_messaged_contacts = 0
        dispatch_complete_contacts = 0
        dispatch_complete_endpoints = 0
        ready_contacts = 0
        blocked_contacts = 0
        total_endpoints = 0
        phone_endpoints = 0
        email_endpoints = 0
        covered_endpoints = 0
        ready_endpoints = 0
        blocked_endpoints = 0
        permanent_failed_endpoints = 0
        excluded_endpoints = 0
        never_attempted_endpoints = 0
        retryable_failed_endpoints = 0
        whatsapp_sent_endpoints = 0
        email_sent_endpoints = 0

        for contact in contacts:
            history = attempts_by_contact.get(contact.contact_id, [])
            metrics = get_contact_endpoint_metrics(contact, history, suppressed)
            endpoint_rows = metrics["whatsapp_endpoints"] + metrics["email_endpoints"]
            if not endpoint_rows:
                contacts_without_valid_endpoints += 1
                continue

            any_sent = False
            all_sent = True
            has_ready = False
            has_blocked = False
            has_dispatch_complete_endpoint = False

            for endpoint in endpoint_rows:
                total_endpoints += 1
                if endpoint["channel"] == Channel.WHATSAPP.value:
                    phone_endpoints += 1
                else:
                    email_endpoints += 1

                coverage_state = endpoint["coverage_state"]
                if coverage_state in {"SENT", "PERMANENT_FAILED", "SUPPRESSED"}:
                    has_dispatch_complete_endpoint = True
                    dispatch_complete_endpoints += 1
                if coverage_state == "SENT":
                    covered_endpoints += 1
                    any_sent = True
                    if endpoint["channel"] == Channel.WHATSAPP.value:
                        whatsapp_sent_endpoints += 1
                    else:
                        email_sent_endpoints += 1
                else:
                    all_sent = False
                    if coverage_state == "PERMANENT_FAILED":
                        permanent_failed_endpoints += 1
                    elif coverage_state == "SUPPRESSED":
                        excluded_endpoints += 1
                    elif coverage_state == "BLOCKED":
                        blocked_endpoints += 1
                        has_blocked = True
                    else:
                        ready_endpoints += 1
                        has_ready = True

                    if endpoint["never_attempted"] and coverage_state != "SUPPRESSED":
                        never_attempted_endpoints += 1
                    if coverage_state == "RETRYABLE" and endpoint["failed_attempt_count"] > 0:
                        retryable_failed_endpoints += 1

            if any_sent:
                contacts_with_any_send += 1
            if all_sent:
                fully_messaged_contacts += 1
            if has_dispatch_complete_endpoint and metrics["is_dispatch_complete"]:
                dispatch_complete_contacts += 1
            if has_ready:
                ready_contacts += 1
            if has_blocked:
                blocked_contacts += 1

        active_endpoints = total_endpoints - excluded_endpoints
        covered_percent = round((covered_endpoints / total_endpoints) * 100, 1) if total_endpoints else 0.0
        failed_attempts = [attempt for attempt in attempts if attempt.status == OutreachStatus.FAILED]
        sent_attempts = [attempt for attempt in attempts if attempt.status == OutreachStatus.SENT]
        unresolved_attempts = [
            attempt
            for attempt in attempts
            if attempt.status in (OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED)
        ]
        pending_attempts = [
            attempt
            for attempt in attempts
            if attempt.status in (OutreachStatus.PREPARED, OutreachStatus.QUEUED, OutreachStatus.SENDING)
        ]
        failed_destinations = {
            self._destination_key(attempt.channel, attempt.destination)
            for attempt in failed_attempts
            if attempt.destination
        }
        sent_destinations = {
            self._destination_key(attempt.channel, attempt.destination)
            for attempt in sent_attempts
            if attempt.destination
        }

        return {
            "total_contacts": total_contacts,
            "contacts_without_valid_endpoints": contacts_without_valid_endpoints,
            "contacts_with_any_send": contacts_with_any_send,
            "fully_messaged_contacts": fully_messaged_contacts,
            "dispatch_complete_contacts": dispatch_complete_contacts,
            "contacts_not_fully_messaged": total_contacts - contacts_without_valid_endpoints - fully_messaged_contacts,
            "ready_contacts": ready_contacts,
            "blocked_contacts": blocked_contacts,
            "total_endpoints": total_endpoints,
            "phone_endpoints": phone_endpoints,
            "email_endpoints": email_endpoints,
            "active_endpoints": active_endpoints,
            "covered_endpoints": covered_endpoints,
            "uncovered_endpoints": active_endpoints - covered_endpoints,
            "ready_endpoints": ready_endpoints,
            "blocked_endpoints": blocked_endpoints,
            "permanent_failed_endpoints": permanent_failed_endpoints,
            "excluded_endpoints": excluded_endpoints,
            "dispatch_complete_endpoints": dispatch_complete_endpoints,
            "never_attempted_endpoints": never_attempted_endpoints,
            "retryable_failed_endpoints": retryable_failed_endpoints,
            "whatsapp_sent_endpoints": whatsapp_sent_endpoints,
            "email_sent_endpoints": email_sent_endpoints,
            "endpoint_coverage_percent": covered_percent,
            "total_attempts": len(attempts),
            "sent_attempts": len(sent_attempts),
            "sent_contacts": len({attempt.contact_id for attempt in sent_attempts}),
            "sent_destinations": len(sent_destinations),
            "failed_attempts": len(failed_attempts),
            "failed_destinations": len(failed_destinations),
            "unresolved_attempts": len(unresolved_attempts),
            "pending_attempts": len(pending_attempts),
            "eligible": ready_contacts,
            "contacted": contacts_with_any_send,
            "whatsapp_sent": whatsapp_sent_endpoints,
            "email_sent": email_sent_endpoints,
            "failed": len(failed_attempts),
            "recovery_required": len(unresolved_attempts),
        }

    @staticmethod
    def _destination_key(channel: Channel, destination: Optional[str]) -> tuple[Channel, str]:
        if channel == Channel.WHATSAPP:
            normalized = normalize_phone_numbers(destination or "")
            return channel, normalized[0] if normalized else (destination or "").strip()
        normalized = normalize_email_addresses(destination or "")
        return channel, normalized[0] if normalized else (destination or "").strip().lower()

    def get_kpis(self, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        now = current_time or self.clock.now()
        contacts = self.contact_repo.list_all()
        coverage = self.get_coverage_snapshot()
        interested = not_interested = interview = follow_up_due = 0

        for contact in contacts:
            if contact.crm_outcome == CRMOutcome.INTERESTED:
                interested += 1
            elif contact.crm_outcome == CRMOutcome.NOT_INTERESTED:
                not_interested += 1

            if contact.interview_status == InterviewState.INTERVIEW:
                interview += 1

            if check_contact_follow_up_eligibility(contact, now, DEFAULT_FOLLOW_UP_THRESHOLD_DAYS).is_due:
                follow_up_due += 1

        return {
            **coverage,
            "interested": interested,
            "not_interested": not_interested,
            "interview": interview,
            "follow_up_due": follow_up_due,
        }
