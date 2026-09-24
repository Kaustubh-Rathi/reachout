"""Campaign execution & control plane application service.

Coordinates dynamic calculation of eligible contacts, company-first round robin,
campaign lifecycle (Start, Pause, Resume) delegating directly to the canonical
PersistentCampaignScheduler, and real-time progress & round metrics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.campaign import Campaign
from app.domain.enums import CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.errors import NotFoundError, OutreachNotReadyError, ValidationError
from app.domain.policies.channel_rotation_policy import ChannelRotationPolicy
from app.domain.policies.prioritization import calculate_company_round_state
from app.ports.infrastructure import CampaignScheduler
from app.services.context import ServiceContext, build_service_context
from app.services.crm_analytics import CrmAnalyticsService


class CampaignService:
    """Application service managing campaign control plane and delegating execution to the canonical scheduler."""

    def __init__(
        self,
        session: Session,
        scheduler: Optional[CampaignScheduler] = None,
        context: Optional[ServiceContext] = None,
    ) -> None:
        self.session = session
        ctx = context or build_service_context(session)
        self.campaign_repo = ctx.campaign_repo
        self.contact_repo = ctx.contact_repo
        self.outreach_repo = ctx.outreach_repo
        self.sender_repo = ctx.sender_repo
        self.template_repo = ctx.template_repo
        self.suppression_repo = ctx.suppression_repo
        self.event_publisher = ctx.event_publisher
        self.scheduler = scheduler or ctx.scheduler
        self.analytics = CrmAnalyticsService(
            contact_repo=self.contact_repo,
            outreach_repo=self.outreach_repo,
            suppression_repo=self.suppression_repo,
            clock=ctx.clock,
        )

    def create_campaign(
        self,
        name: str,
        channel: Channel,
        template_ids: Optional[List[str]] = None,
        sender_account_ids: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        automatic_quota: Optional[int] = None,
        manual_reserve: int = 20,
    ) -> Campaign:
        """Create a new campaign entity."""
        campaign = Campaign.create(
            name=name,
            channel=channel,
            template_ids=template_ids or [],
            sender_account_ids=sender_account_ids or [],
            metadata=metadata or {},
            automatic_quota=automatic_quota,
            manual_reserve=manual_reserve,
        )
        self.campaign_repo.save(campaign)
        self.session.commit()
        return campaign

    def list_campaigns(self) -> List[Dict[str, Any]]:
        """List all campaigns with aggregated progress."""
        campaigns = self.campaign_repo.list_all()
        coverage = self.analytics.get_coverage_snapshot()
        return [self.get_campaign_progress(c.id, coverage_snapshot=coverage) for c in campaigns]

    def get_campaign_progress(
        self,
        campaign_id: str,
        coverage_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Aggregate real-time campaign statistics, round metrics, and quotas from database state."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise NotFoundError(f"Campaign not found: {campaign_id}")

        attempts = self.outreach_repo.list_by_campaign(campaign_id)
        total = len(attempts)
        sent = sum(1 for attempt in attempts if attempt.status == OutreachStatus.SENT)
        pending = sum(
            1
            for attempt in attempts
            if attempt.status in (OutreachStatus.PREPARED, OutreachStatus.QUEUED, OutreachStatus.SENDING)
        )
        failed = sum(1 for attempt in attempts if attempt.status == OutreachStatus.FAILED)
        unresolved = sum(
            1 for attempt in attempts if attempt.status in (OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED)
        )
        attempt_success_percent = round((sent / total) * 100, 1) if total else 0.0
        target_count = campaign.automatic_quota
        target_successes = campaign.automatic_used
        target_progress_percent = (
            min(100.0, round((target_successes / target_count) * 100, 1))
            if target_count is not None and target_count > 0
            else None
        )
        sent_contacts = len({attempt.contact_id for attempt in attempts if attempt.status == OutreachStatus.SENT})
        sent_destinations = len(
            {
                (attempt.channel.value, (attempt.destination or "").strip().lower())
                for attempt in attempts
                if attempt.status == OutreachStatus.SENT and attempt.destination
            }
        )
        failed_destinations = len(
            {
                (attempt.channel.value, (attempt.destination or "").strip().lower())
                for attempt in attempts
                if attempt.status == OutreachStatus.FAILED and attempt.destination
            }
        )
        coverage = coverage_snapshot or self.analytics.get_coverage_snapshot()

        # Calculate company round metrics
        all_contacts = self.contact_repo.list_all()
        dispatched_ids = {a.contact_id for a in attempts if a.status == OutreachStatus.SENT}
        round_metrics = calculate_company_round_state(
            all_contacts=all_contacts,
            dispatched_contact_ids=dispatched_ids,
        )

        last_attempt = attempts[-1] if attempts else None

        return {
            "id": campaign.id,
            "name": campaign.name,
            "channel": campaign.channel.value,
            "status": campaign.status.value,
            "total": total,
            "completed": sent,
            "pending": pending,
            "failed": failed,
            "recovery_required": unresolved,
            "progress_percent": attempt_success_percent,
            "attempt_success_percent": attempt_success_percent,
            "target_count": target_count,
            "target_progress_percent": target_progress_percent,
            "sent_contacts": sent_contacts,
            "sent_destinations": sent_destinations,
            "failed_destinations": failed_destinations,
            "attempt_outcomes": {
                "total_attempts": total,
                "sent": sent,
                "failed": failed,
                "pending": pending,
                "unresolved": unresolved,
                "sent_percent": attempt_success_percent,
            },
            "target_progress": {
                "target_count": target_count,
                "successful_sends": target_successes,
                "remaining_sends": max(0, target_count - target_successes) if target_count is not None else None,
                "completed_percent": target_progress_percent,
                "basis": "successful_sends" if target_count is not None else "target_not_recorded",
            },
            "global_endpoint_coverage": {
                "total_endpoints": coverage["total_endpoints"],
                "covered_endpoints": coverage["covered_endpoints"],
                "uncovered_endpoints": coverage["uncovered_endpoints"],
                "coverage_percent": coverage["endpoint_coverage_percent"],
                "ready_endpoints": coverage["ready_endpoints"],
                "blocked_endpoints": coverage["blocked_endpoints"],
                "never_attempted_endpoints": coverage["never_attempted_endpoints"],
                "retryable_failed_endpoints": coverage["retryable_failed_endpoints"],
                "permanent_failed_endpoints": coverage["permanent_failed_endpoints"],
            },
            "automatic_quota": campaign.automatic_quota,
            "manual_reserve": campaign.manual_reserve,
            "automatic_used": campaign.automatic_used,
            "manual_used": campaign.manual_used,
            "remaining_automatic": campaign.remaining_automatic,
            "remaining_manual": campaign.remaining_manual,
            "current_round": round_metrics.current_round,
            "total_rounds": round_metrics.total_rounds,
            "companies_total": round_metrics.companies_total,
            "companies_covered": round_metrics.companies_covered_total,
            "companies_remaining": round_metrics.companies_with_remaining_contacts,
            "current_dispatch_channel": last_attempt.channel.value if last_attempt else campaign.channel.value,
            "current_sender": last_attempt.sender_account_id
            if last_attempt
            else (campaign.sender_account_ids[0] if campaign.sender_account_ids else "AUTO"),
            "template_used": last_attempt.template_id
            if last_attempt
            else (campaign.template_ids[0] if campaign.template_ids else "AUTO"),
            "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
            "ended_at": campaign.ended_at.isoformat() if campaign.ended_at else None,
            "created_at": campaign.created_at.isoformat(),
        }

    def validate_outreach_readiness(
        self,
        channel: Channel,
        campaign_id: Optional[str] = None,
        is_resuming: bool = False,
    ) -> Dict[str, Any]:
        """Verify all domain readiness prerequisites before initiating or resuming automated outreach."""
        campaign: Optional[Campaign] = None
        if campaign_id:
            campaign = self.campaign_repo.get_by_id(campaign_id)
            if not campaign:
                return {
                    "ready": False,
                    "reason": "CAMPAIGN_NOT_FOUND",
                    "detail": f"Campaign '{campaign_id}' does not exist.",
                }
            if not is_resuming and campaign.status == CampaignStatus.RUNNING:
                return {
                    "ready": False,
                    "reason": "CAMPAIGN_ALREADY_RUNNING",
                    "detail": f"Campaign '{campaign_id}' is already actively running.",
                }
            if is_resuming and campaign.status != CampaignStatus.PAUSED:
                return {
                    "ready": False,
                    "reason": "CAMPAIGN_NOT_PAUSED",
                    "detail": f"Campaign '{campaign_id}' is in status '{campaign.status.value}', cannot resume.",
                }
            if not campaign.can_dispatch_automatic():
                return {
                    "ready": False,
                    "reason": "QUOTA_EXHAUSTED",
                    "detail": "Campaign automatic outreach quota is already exhausted.",
                }

        # 2. Check contacts exist in database
        contacts = self.contact_repo.list_all()
        if not contacts:
            return {
                "ready": False,
                "reason": "NO_CONTACTS",
                "detail": "No contacts exist in the operational database. Please sync contacts from Excel source first.",
            }

        allowed_sender_ids = set(campaign.sender_account_ids or ()) if campaign else set()
        allowed_template_ids = set(campaign.template_ids or ()) if campaign else set()
        active_senders = [
            sender
            for sender in self.sender_repo.list_active()
            if sender.status == SenderStatus.ACTIVE and (not allowed_sender_ids or sender.id in allowed_sender_ids)
        ]
        if not active_senders:
            reason = "NO_ACTIVE_WHATSAPP_SESSION" if channel == Channel.WHATSAPP else "NO_ACTIVE_EMAIL_SESSION"
            return {
                "ready": False,
                "reason": reason,
                "detail": "No active sender sessions matching the campaign allocation are available.",
            }

        available_channels = set()
        for sender in active_senders:
            templates = [
                template
                for template in self.template_repo.list_by_channel(sender.channel, active_only=True)
                if not allowed_template_ids or template.id in allowed_template_ids
            ]
            if not templates:
                templates = [
                    template
                    for template in self.template_repo.list_by_channel(sender.channel, active_only=False)
                    if not allowed_template_ids or template.id in allowed_template_ids
                ]
            if templates:
                available_channels.add(sender.channel)
        if not available_channels:
            return {
                "ready": False,
                "reason": "MISSING_TEMPLATES",
                "detail": "No active sender has an available template matching the campaign allocation.",
            }

        # 5. Check if any eligible uncovered endpoints remain
        attempts = self.outreach_repo.list_all()
        attempts_by_contact = {}
        for a in attempts:
            attempts_by_contact.setdefault(a.contact_id, []).append(a)

        suppressed_set = {s.identifier for s in self.suppression_repo.list_all()}

        has_eligible_contact = any(
            ChannelRotationPolicy.evaluate_contact_dispatch(
                contact=contact,
                preferred_channel=channel,
                historical_attempts=attempts_by_contact.get(contact.contact_id, []),
                suppressed_identifiers=suppressed_set,
                available_channels=available_channels,
            ).is_eligible
            for contact in contacts
        )

        if not has_eligible_contact:
            return {
                "ready": False,
                "reason": "ALL_CONTACTS_COVERED",
                "detail": "No contact currently has an uncovered endpoint eligible for automatic dispatch.",
            }

        return {
            "ready": True,
            "reason": None,
            "detail": "Outreach prerequisites satisfied. Ready to start.",
        }

    def start_campaign(self, campaign_id: str, max_count: Optional[int] = None) -> Dict[str, Any]:
        """Start campaign execution via canonical PersistentCampaignScheduler with strict readiness gate."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise NotFoundError(f"Campaign not found: {campaign_id}")

        # Strict Readiness Check before starting
        readiness = self.validate_outreach_readiness(campaign.channel, campaign_id=campaign_id)
        if not readiness["ready"]:
            raise OutreachNotReadyError(readiness["reason"], readiness["detail"])

        if max_count is not None:
            campaign.metadata["automatic_quota"] = int(max_count)
            campaign.metadata["target_kind"] = "successful_sends"
            self.campaign_repo.save(campaign)
            self.session.commit()

        self.scheduler.start_campaign(campaign_id=campaign_id, max_count=max_count)
        return self.get_campaign_progress(campaign_id)

    def pause_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Pause a running campaign via canonical PersistentCampaignScheduler."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise NotFoundError(f"Campaign not found: {campaign_id}")

        self.scheduler.pause_campaign(campaign_id)
        return self.get_campaign_progress(campaign_id)

    def resume_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Resume a paused campaign via canonical PersistentCampaignScheduler with strict readiness gate."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise NotFoundError(f"Campaign not found: {campaign_id}")

        if campaign.status != CampaignStatus.PAUSED:
            raise ValidationError(f"Cannot resume campaign in status '{campaign.status.value}'. Must be PAUSED.")

        # Strict readiness gate check before resuming
        readiness = self.validate_outreach_readiness(campaign.channel, campaign_id=campaign_id, is_resuming=True)
        if not readiness["ready"]:
            raise OutreachNotReadyError(readiness["reason"], readiness["detail"])

        self.scheduler.resume_campaign(campaign_id)
        return self.get_campaign_progress(campaign_id)
