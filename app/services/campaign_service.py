"""Campaign execution & control plane application service.

Coordinates dynamic calculation of eligible contacts, company-first round robin,
campaign lifecycle (Start, Pause, Resume, Stop) delegating directly to the canonical
PersistentCampaignScheduler, and real-time progress & round metrics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domain.campaign import Campaign
from app.domain.enums import CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.policies.endpoint_coverage_policy import is_contact_fully_covered
from app.domain.policies.prioritization import calculate_company_round_state
from app.infrastructure.scheduler.campaign_scheduler import get_campaign_scheduler
from app.ports.infrastructure import CampaignScheduler
from app.services.context import ServiceContext, build_service_context


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
        self.scheduler = scheduler or get_campaign_scheduler()

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
        return [self.get_campaign_progress(c.id) for c in campaigns]

    def get_campaign(self, campaign_id: str) -> Optional[Campaign]:
        """Get campaign entity by ID."""
        return self.campaign_repo.get_by_id(campaign_id)

    def get_campaign_progress(self, campaign_id: str) -> Dict[str, Any]:
        """Aggregate real-time campaign statistics, round metrics, and quotas from database state."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        attempts = self.outreach_repo.list_by_campaign(campaign_id)
        total = len(attempts)
        completed = sum(1 for a in attempts if a.status == OutreachStatus.SENT)
        pending = sum(
            1 for a in attempts if a.status in (OutreachStatus.PREPARED, OutreachStatus.QUEUED, OutreachStatus.SENDING)
        )
        failed = sum(1 for a in attempts if a.status == OutreachStatus.FAILED)
        unknown_recovery = sum(
            1 for a in attempts if a.status in (OutreachStatus.UNKNOWN, OutreachStatus.RECOVERY_REQUIRED)
        )

        pct = round((completed / total) * 100, 1) if total > 0 else 0.0

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
            "completed": completed,
            "pending": pending,
            "failed": failed,
            "recovery_required": unknown_recovery,
            "progress_percent": pct,
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
        # 1. Check campaign if given
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

        # 3. Check templates exist
        templates = self.template_repo.list_by_channel(channel)
        if not templates:
            return {
                "ready": False,
                "reason": "MISSING_TEMPLATES",
                "detail": f"No active message templates found for channel '{channel.value}'.",
            }

        # 4. Check active sender sessions
        if channel == Channel.WHATSAPP:
            active_wa = [s for s in self.sender_repo.list_active(Channel.WHATSAPP) if s.status == SenderStatus.ACTIVE]
            if not active_wa:
                return {
                    "ready": False,
                    "reason": "NO_ACTIVE_WHATSAPP_SESSION",
                    "detail": "No active WhatsApp sender session available. Please scan the QR code to authenticate a session in the Senders panel.",
                }
        elif channel == Channel.EMAIL:
            active_em = [s for s in self.sender_repo.list_active(Channel.EMAIL) if s.status == SenderStatus.ACTIVE]
            if not active_em:
                return {
                    "ready": False,
                    "reason": "NO_ACTIVE_EMAIL_SESSION",
                    "detail": "No active Email sender session available. Please configure and verify an email sender in the Senders panel.",
                }

        # 5. Check if any eligible uncovered endpoints remain
        attempts = self.outreach_repo.list_all()
        attempts_by_contact = {}
        for a in attempts:
            attempts_by_contact.setdefault(a.contact_id, []).append(a)

        suppressed_set = {s.identifier for s in self.suppression_repo.list_all()}

        has_uncovered = False
        for c in contacts:
            c_hist = attempts_by_contact.get(c.contact_id, [])
            if not is_contact_fully_covered(c, c_hist, suppressed_set):
                has_uncovered = True
                break

        if not has_uncovered:
            return {
                "ready": False,
                "reason": "ALL_CONTACTS_COVERED",
                "detail": "All contacts and endpoints in the database are already fully covered.",
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
            raise ValueError(f"Campaign not found: {campaign_id}")

        # Strict Readiness Check before starting
        readiness = self.validate_outreach_readiness(campaign.channel, campaign_id=campaign_id)
        if not readiness["ready"]:
            raise ValueError(f"OUTREACH_NOT_READY: {readiness['reason']} - {readiness['detail']}")

        # Delegate execution directly to the canonical scheduler
        self.scheduler.start_campaign(campaign_id=campaign_id, max_count=max_count)
        return self.get_campaign_progress(campaign_id)

    def pause_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Pause a running campaign via canonical PersistentCampaignScheduler."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        self.scheduler.pause_campaign(campaign_id)
        return self.get_campaign_progress(campaign_id)

    def resume_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Resume a paused campaign via canonical PersistentCampaignScheduler with strict readiness gate."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        if campaign.status != CampaignStatus.PAUSED:
            raise ValueError(f"Cannot resume campaign in status '{campaign.status.value}'. Must be PAUSED.")

        # Strict readiness gate check before resuming
        readiness = self.validate_outreach_readiness(campaign.channel, campaign_id=campaign_id, is_resuming=True)
        if not readiness["ready"]:
            raise ValueError(f"OUTREACH_NOT_READY: {readiness['reason']} - {readiness['detail']}")

        self.scheduler.resume_campaign(campaign_id)
        return self.get_campaign_progress(campaign_id)

    def stop_campaign(self, campaign_id: str) -> Dict[str, Any]:
        """Stop/cancel a campaign permanently via canonical PersistentCampaignScheduler."""
        campaign = self.campaign_repo.get_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        self.scheduler.stop_campaign(campaign_id)
        return self.get_campaign_progress(campaign_id)
