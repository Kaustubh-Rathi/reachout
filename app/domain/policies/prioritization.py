"""Company-First Outreach Prioritization Policy.

Ensures outreach dispatch intersperses contacts across different companies
(round-robin) rather than exhausting multiple contacts at the same company
consecutively.

Pattern: A1, B1, C1, D1, A2, B2, C2, D2, A3, C3... NEVER A1, A2, A3, B1...
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Container, Dict, Iterable, List, Optional, Sequence, Set

from app.domain.contact import Contact


@dataclass(frozen=True)
class CompanyRoundMetrics:
    """Snapshot of company round-robin progress."""

    current_round: int
    total_rounds: int
    companies_total: int
    companies_covered_total: int
    companies_covered_current_round: int
    companies_remaining_current_round: int
    companies_with_remaining_contacts: int
    total_eligible_contacts: int
    remaining_eligible_contacts: int


def prioritize_company_first(
    contacts: Sequence[Contact],
    eligibility_predicate: Optional[Callable[[Contact], bool]] = None,
    company_key_fn: Optional[Callable[[Contact], str]] = None,
    dispatched_contact_ids: Optional[Container[str]] = None,
) -> List[Contact]:
    """Deterministically order contacts by interleaving one contact per company per round.

    Round-aware Algorithm:
    1. Group all contacts by company while preserving initial arrival / discovery order.
    2. For each company, compute total dispatches already performed across all company contacts.
    3. Filter eligible remaining contacts for that company in deterministic arrival order.
    4. For eligible contacts [c0, c1, c2...], assign strictly increasing round numbers:
       round_num = comp_dispatches + offset + 1.
    5. Order remaining contacts by (round_num, company_discovery_index, contact_offset).
    6. For N eligible companies, the first N dispatches are guaranteed to be distributed across
       distinct companies whenever each company has an eligible endpoint.

    Args:
        contacts: Raw sequence of candidate contacts.
        eligibility_predicate: Optional function returning True if contact is eligible.
        company_key_fn: Optional key extractor (defaults to `c.company_id.casefold()`).
        dispatched_contact_ids: Optional set/dict of contact IDs or attempts already dispatched.

    Returns:
        A newly constructed list of eligible contacts ordered company-first round-robin.
    """
    key_extractor = company_key_fn or (lambda c: c.company_id.strip().casefold())
    dispatched = dispatched_contact_ids or set()

    if not contacts:
        return []

    # 1. Group all contacts by company in discovery order
    company_discovery_order: List[str] = []
    company_all_contacts: OrderedDict[str, List[Contact]] = OrderedDict()

    for contact in contacts:
        ckey = key_extractor(contact)
        if ckey not in company_all_contacts:
            company_all_contacts[ckey] = []
            company_discovery_order.append(ckey)
        company_all_contacts[ckey].append(contact)

    # 2. For each company, compute total dispatches and assign rounds to eligible contacts
    company_remaining_with_rounds: List[tuple[int, int, int, Contact]] = []

    for comp_idx, ckey in enumerate(company_discovery_order):
        comp_contacts = company_all_contacts[ckey]

        # Calculate total dispatches already performed for this entire company
        comp_dispatches = 0
        for cnt in comp_contacts:
            cnt_touches = 0
            if isinstance(dispatched, dict):
                v = dispatched.get(cnt.contact_id, 0)
                cnt_touches = len(v) if isinstance(v, (list, tuple, set)) else int(v)
            elif isinstance(dispatched, (list, tuple)):
                cnt_touches = dispatched.count(cnt.contact_id)
            elif cnt.contact_id in dispatched:
                cnt_touches = 1

            if cnt_touches == 0 and (cnt.last_whatsapp_at is not None or cnt.last_email_at is not None):
                cnt_touches = 1

            comp_dispatches += cnt_touches

        # Find eligible remaining contacts in company
        eligible_remaining: List[Contact] = []
        for cnt in comp_contacts:
            if eligibility_predicate is not None:
                if eligibility_predicate(cnt):
                    eligible_remaining.append(cnt)
            else:
                is_done = (
                    cnt.contact_id in dispatched or cnt.last_whatsapp_at is not None or cnt.last_email_at is not None
                )
                if not is_done:
                    eligible_remaining.append(cnt)

        # Assign strictly increasing round numbers per company
        for offset, cnt in enumerate(eligible_remaining):
            round_num = comp_dispatches + offset + 1
            company_remaining_with_rounds.append((round_num, comp_idx, offset, cnt))

    # 3. Sort by (round_num, comp_idx, offset)
    company_remaining_with_rounds.sort(key=lambda item: (item[0], item[1], item[2]))

    return [item[3] for item in company_remaining_with_rounds]


def calculate_company_round_state(
    all_contacts: Sequence[Contact],
    eligibility_predicate: Optional[Callable[[Contact], bool]] = None,
    dispatched_contact_ids: Optional[Any] = None,
    company_key_fn: Optional[Callable[[Contact], str]] = None,
) -> CompanyRoundMetrics:
    """Calculate real-time company round-robin metrics and coverage."""
    key_extractor = company_key_fn or (lambda c: c.company_id.strip().casefold())
    dispatched = dispatched_contact_ids or set()

    # All unique companies from input in order
    all_company_keys: OrderedDict[str, List[Contact]] = OrderedDict()
    for c in all_contacts:
        ckey = key_extractor(c)
        if ckey not in all_company_keys:
            all_company_keys[ckey] = []
        all_company_keys[ckey].append(c)

    companies_total = len(all_company_keys)
    if companies_total == 0:
        return CompanyRoundMetrics(
            current_round=1,
            total_rounds=1,
            companies_total=0,
            companies_covered_total=0,
            companies_covered_current_round=0,
            companies_remaining_current_round=0,
            companies_with_remaining_contacts=0,
            total_eligible_contacts=0,
            remaining_eligible_contacts=0,
        )

    # Prioritize all remaining
    prioritized_remaining = prioritize_company_first(
        contacts=all_contacts,
        eligibility_predicate=eligibility_predicate,
        company_key_fn=key_extractor,
        dispatched_contact_ids=dispatched,
    )

    # Prioritize total eligible ignoring dispatched set for total count
    all_eligible = prioritize_company_first(
        contacts=all_contacts,
        eligibility_predicate=eligibility_predicate,
        company_key_fn=key_extractor,
        dispatched_contact_ids=set(),
    )

    total_eligible = len(all_eligible)
    remaining_eligible = len(prioritized_remaining)

    # Calculate per-company dispatch counts and depth
    comp_dispatched_counts: Dict[str, int] = {}
    comp_remaining_counts: Dict[str, int] = {}

    for ckey, comp_contacts in all_company_keys.items():
        comp_disp = 0
        comp_rem = 0
        for cnt in comp_contacts:
            cnt_touches = 0
            if isinstance(dispatched, dict):
                v = dispatched.get(cnt.contact_id, 0)
                cnt_touches = len(v) if isinstance(v, (list, tuple, set)) else int(v)
            elif isinstance(dispatched, (list, tuple)):
                cnt_touches = dispatched.count(cnt.contact_id)
            elif cnt.contact_id in dispatched:
                cnt_touches = 1

            if cnt_touches == 0 and (cnt.last_whatsapp_at is not None or cnt.last_email_at is not None):
                cnt_touches = 1

            comp_disp += cnt_touches

            if eligibility_predicate is not None:
                if eligibility_predicate(cnt):
                    comp_rem += 1
            else:
                if cnt_touches == 0:
                    comp_rem += 1

        comp_dispatched_counts[ckey] = comp_disp
        comp_remaining_counts[ckey] = comp_rem

    # Calculate total rounds and current round
    total_rounds_calc = [comp_dispatched_counts[ckey] + comp_remaining_counts[ckey] for ckey in all_company_keys]
    total_rounds = max(total_rounds_calc) if total_rounds_calc else 1
    total_rounds = max(1, total_rounds)

    if prioritized_remaining:
        active_rounds = [comp_dispatched_counts.get(key_extractor(c), 0) + 1 for c in prioritized_remaining]
        current_round = min(active_rounds) if active_rounds else 1
    else:
        current_round = total_rounds

    # Covered companies
    companies_covered_total = sum(1 for ckey in all_company_keys if comp_dispatched_counts[ckey] > 0)
    companies_with_remaining = sum(1 for ckey in all_company_keys if comp_remaining_counts[ckey] > 0)

    covered_in_curr_round = sum(1 for ckey in all_company_keys if comp_dispatched_counts[ckey] >= current_round)
    remaining_in_curr_round = companies_total - covered_in_curr_round

    return CompanyRoundMetrics(
        current_round=current_round,
        total_rounds=total_rounds,
        companies_total=companies_total,
        companies_covered_total=companies_covered_total,
        companies_covered_current_round=covered_in_curr_round,
        companies_remaining_current_round=remaining_in_curr_round,
        companies_with_remaining_contacts=companies_with_remaining,
        total_eligible_contacts=total_eligible,
        remaining_eligible_contacts=remaining_eligible,
    )


class ContactPrioritizer:
    """Encapsulates WHO selection: Company-First Round-Robin prioritization with eligibility filtering."""

    def __init__(self, company_key_fn: Optional[Callable[[Contact], str]] = None) -> None:
        self.company_key_fn = company_key_fn or (lambda c: c.company_id.strip().casefold())

    def get_ordered_candidates(
        self,
        contacts: Sequence[Contact],
        eligibility_predicate: Optional[Callable[[Contact], bool]] = None,
        dispatched_contact_ids: Optional[Container[str]] = None,
    ) -> List[Contact]:
        return prioritize_company_first(
            contacts=contacts,
            eligibility_predicate=eligibility_predicate,
            company_key_fn=self.company_key_fn,
            dispatched_contact_ids=dispatched_contact_ids,
        )

    def get_round_metrics(
        self,
        all_contacts: Sequence[Contact],
        eligibility_predicate: Optional[Callable[[Contact], bool]] = None,
        dispatched_contact_ids: Optional[Set[str]] = None,
    ) -> CompanyRoundMetrics:
        return calculate_company_round_state(
            all_contacts=all_contacts,
            eligibility_predicate=eligibility_predicate,
            dispatched_contact_ids=dispatched_contact_ids,
            company_key_fn=self.company_key_fn,
        )
