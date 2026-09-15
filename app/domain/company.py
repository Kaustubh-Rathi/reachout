"""Company domain entity."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    from app.domain.enums import CompanyStatus


def normalize_company_name(name: str) -> str:
    """Normalize company name for deterministic identity matching."""
    if not name:
        return ""
    cleaned = re.sub(r"\s+", " ", str(name)).strip()
    return cleaned.casefold()


@dataclass
class Company:
    """Represents an employer organization or target company.

    Attributes:
        id: Stable unique identifier for the company.
        name: Canonical display name (e.g. 'Google', 'Amazon').
        normalized_name: Lowercased, whitespace-stripped name for matching.
        domain: Optional primary web domain (e.g. 'amazon.com').
        created_at: Entity creation timestamp.
        updated_at: Entity last-modification timestamp.
    """

    id: str
    name: str
    normalized_name: str = field(default="")
    domain: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.normalized_name:
            self.normalized_name = normalize_company_name(self.name)
        if not self.id:
            # Deterministic slug or random fallback
            slug = re.sub(r"[^a-z0-9]+", "-", self.normalized_name).strip("-")
            self.id = slug if slug else f"company_{uuid.uuid4().hex[:12]}"

    @classmethod
    def create(
        cls,
        name: str,
        company_id: Optional[str] = None,
        domain: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> Company:
        now = created_at or datetime.now(timezone.utc)
        normalized = normalize_company_name(name)
        cid = company_id
        if not cid:
            slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
            cid = slug if slug else f"company_{uuid.uuid4().hex[:12]}"
        return cls(
            id=cid,
            name=name.strip(),
            normalized_name=normalized,
            domain=domain.strip().lower() if domain else None,
            created_at=now,
            updated_at=now,
        )

    def update_name(self, new_name: str, timestamp: Optional[datetime] = None) -> None:
        """Update company name and refresh normalized name."""
        self.name = new_name.strip()
        self.normalized_name = normalize_company_name(new_name)
        self.updated_at = timestamp or datetime.now(timezone.utc)


def calculate_company_status(
    contacts: Sequence[Any],
    historical_attempts: Optional[Sequence[Any]] = None,
    suppressed_identifiers: Optional[Any] = None,
    is_closed: bool = False,
) -> CompanyStatus:
    """Calculate company-level aggregate status according to prompt Requirement 13.

    Statuses:
    - CLOSED (⚫ CLOSED): Operator explicitly closed or all contacts opted out / DNC / closed.
    - NOT_CONTACTED (🔴 NOT_CONTACTED): No eligible HR endpoint has been successfully contacted.
    - IN_PROGRESS (🟡 IN PROGRESS): At least one endpoint has been contacted but coverage is incomplete.
    - CONTACTED (🟢 CONTACTED): Required outreach coverage has been completed (all eligible endpoints contacted).
    """
    from app.domain.enums import CompanyStatus, CRMOutcome
    from app.domain.policies.endpoint_coverage_policy import is_endpoint_covered

    if is_closed:
        return CompanyStatus.CLOSED

    if not contacts:
        return CompanyStatus.NOT_CONTACTED

    # Check if all contacts are DNC/closed
    all_dnc = True
    for c in contacts:
        opt_out_tags = {"dnc", "opt_out", "opt-out", "do_not_contact", "unsubscribed", "closed"}
        c_tags = {t.strip().lower() for t in (c.tags or [])}
        c_outcome = getattr(c, "crm_outcome", CRMOutcome.NONE)
        if c_outcome not in (CRMOutcome.DO_NOT_CONTACT, CRMOutcome.CLOSED) and not (c_tags & opt_out_tags):
            all_dnc = False
            break
    if all_dnc and len(contacts) > 0:
        return CompanyStatus.CLOSED

    total_endpoints = 0
    covered_endpoints = 0

    for c in contacts:
        eps = c.endpoints if hasattr(c, "endpoints") else []
        for ep in eps:
            total_endpoints += 1
            if is_endpoint_covered(ep, c.contact_id, historical_attempts, c):
                covered_endpoints += 1

    if covered_endpoints == 0:
        return CompanyStatus.NOT_CONTACTED
    elif total_endpoints > 0 and covered_endpoints >= total_endpoints:
        return CompanyStatus.CONTACTED
    else:
        return CompanyStatus.IN_PROGRESS
