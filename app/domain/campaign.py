"""Campaign domain entity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.domain.enums import CampaignStatus, Channel
from app.domain.errors import ValidationError


@dataclass
class Campaign:
    """Represents a scheduled or executing batch outreach initiative.

    Attributes:
        id: Stable unique campaign identifier.
        name: Human-friendly campaign name.
        channel: Delivery channel for all operations in this campaign.
        status: Campaign execution lifecycle state.
        template_ids: List of template IDs participating in rotation.
        sender_account_ids: List of sender accounts allocated to this campaign.
        created_at: Creation timestamp.
        started_at: Timestamp when campaign execution first began.
        ended_at: Timestamp when campaign reached terminal state.
        metadata: Extensible configuration settings (quotas, delays, batch sizes, tags).
    """

    id: str
    name: str
    channel: Channel
    status: CampaignStatus = CampaignStatus.IDLE
    template_ids: List[str] = field(default_factory=list)
    sender_account_ids: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"cmp_{self.channel.value.lower()}_{uuid.uuid4().hex[:12]}"

    @property
    def automatic_quota(self) -> Optional[int]:
        """Configured cap for automated dispatches in this campaign."""
        val = self.metadata.get("automatic_quota")
        return int(val) if val is not None else None

    @property
    def manual_reserve(self) -> int:
        """Configured reserve for manual sends."""
        return int(self.metadata.get("manual_reserve", 20))

    @property
    def automatic_used(self) -> int:
        """Number of successful automated dispatches completed."""
        return int(self.metadata.get("automatic_used", 0))

    @property
    def manual_used(self) -> int:
        """Number of manual sends performed."""
        return int(self.metadata.get("manual_used", 0))

    @property
    def remaining_automatic(self) -> Optional[int]:
        """Remaining capacity for automated dispatches."""
        if self.automatic_quota is None:
            return None
        return max(0, self.automatic_quota - self.automatic_used)

    @property
    def remaining_manual(self) -> int:
        """Remaining manual reserve capacity."""
        return max(0, self.manual_reserve - self.manual_used)

    def can_dispatch_automatic(self) -> bool:
        """Check whether campaign has remaining automated quota."""
        if self.automatic_quota is None:
            return True
        return self.automatic_used < self.automatic_quota

    @classmethod
    def create(
        cls,
        name: str,
        channel: Channel,
        template_ids: Optional[List[str]] = None,
        sender_account_ids: Optional[List[str]] = None,
        campaign_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        automatic_quota: Optional[int] = None,
        manual_reserve: int = 20,
    ) -> Campaign:
        cid = campaign_id or f"cmp_{channel.value.lower()}_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        meta = dict(metadata or {})
        if automatic_quota is not None:
            meta["automatic_quota"] = int(automatic_quota)
        if "manual_reserve" not in meta:
            meta["manual_reserve"] = int(manual_reserve)
        if "automatic_used" not in meta:
            meta["automatic_used"] = 0
        if "manual_used" not in meta:
            meta["manual_used"] = 0

        return cls(
            id=cid,
            name=name.strip(),
            channel=channel,
            status=CampaignStatus.IDLE,
            template_ids=list(template_ids or []),
            sender_account_ids=list(sender_account_ids or []),
            created_at=now,
            started_at=None,
            ended_at=None,
            metadata=meta,
        )

    def start(self, timestamp: Optional[datetime] = None) -> None:
        """Start the campaign execution."""
        if self.status not in (CampaignStatus.IDLE, CampaignStatus.STARTING):
            raise ValidationError(f"Cannot start campaign in status '{self.status}'")
        now = timestamp or datetime.now(timezone.utc)
        self.status = CampaignStatus.RUNNING
        if not self.started_at:
            self.started_at = now

    def pause(self) -> None:
        """Pause active campaign."""
        if self.status != CampaignStatus.RUNNING:
            raise ValidationError(f"Cannot pause campaign in status '{self.status}'")
        self.status = CampaignStatus.PAUSED

    def resume(self) -> None:
        """Resume paused campaign."""
        if self.status != CampaignStatus.PAUSED:
            raise ValidationError(f"Cannot resume campaign in status '{self.status}'")
        self.status = CampaignStatus.RUNNING

    def complete(self, timestamp: Optional[datetime] = None) -> None:
        """Mark campaign as successfully completed."""
        if self.status != CampaignStatus.RUNNING:
            raise ValidationError(f"Cannot complete campaign in status '{self.status}'")
        self.status = CampaignStatus.COMPLETED
        self.ended_at = timestamp or datetime.now(timezone.utc)

    def fail(self, reason: str, timestamp: Optional[datetime] = None) -> None:
        """Mark campaign as failed with diagnostics."""
        self.status = CampaignStatus.FAILED
        self.ended_at = timestamp or datetime.now(timezone.utc)
        self.metadata["failure_reason"] = reason
