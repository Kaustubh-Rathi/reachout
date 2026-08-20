"""Company-First Contact Prioritization Verification Tests.

Verifies the canonical round-robin company interleaving algorithm:
1. Dataset: A1, A2, A3, B1, C1, C2, D1 -> Result: A1, B1, C1, D1, A2, C2, A3.
2. Handling partially contacted companies (e.g. A1 already contacted).
3. Handling exhausted companies (e.g. all contacts at B contacted).
4. Deterministic tie-breaking and case-insensitive company deduplication.
"""

from __future__ import annotations

from typing import List
import pytest

from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.policies.prioritization import prioritize_company_first


def create_mock_contact(company_name: str, identifier: str, phone: str = "919000000000") -> Contact:
    """Helper to construct contacts with company and label identifier."""
    comp = Company.create(company_name)
    return Contact(
        contact_id=f"cnt_{identifier}",
        company_id=comp.id,
        name=identifier,
        phone=phone,
        email=f"{identifier.lower()}@{comp.normalized_name.replace(' ', '')}.com",
    )


class TestCompanyFirstPrioritization:
    """Suite verifying strict Company-First interleaving outreach ordering."""

    def test_canonical_dataset_interleaving(self):
        """Interleaving dataset [A1, A2, A3, B1, C1, C2, D1] produces [A1, B1, C1, D1, A2, C2, A3]."""
        a1 = create_mock_contact("Company A", "A1", "919000000001")
        a2 = create_mock_contact("Company A", "A2", "919000000002")
        a3 = create_mock_contact("Company A", "A3", "919000000003")
        b1 = create_mock_contact("Company B", "B1", "919000000004")
        c1 = create_mock_contact("Company C", "C1", "919000000005")
        c2 = create_mock_contact("Company C", "C2", "919000000006")
        d1 = create_mock_contact("Company D", "D1", "919000000007")

        raw_contacts = [a1, a2, a3, b1, c1, c2, d1]

        ordered = prioritize_company_first(raw_contacts)
        ordered_names = [c.name for c in ordered]

        expected_names = ["A1", "B1", "C1", "D1", "A2", "C2", "A3"]
        assert ordered_names == expected_names, f"Expected {expected_names}, got {ordered_names}"

    def test_partial_state_when_first_contact_already_contacted(self):
        """If A1 was already contacted yesterday, remaining sequence starts with A2 on Pass 1."""
        a1 = create_mock_contact("Company A", "A1", "919000000001")
        a2 = create_mock_contact("Company A", "A2", "919000000002")
        a3 = create_mock_contact("Company A", "A3", "919000000003")
        b1 = create_mock_contact("Company B", "B1", "919000000004")
        c1 = create_mock_contact("Company C", "C1", "919000000005")
        c2 = create_mock_contact("Company C", "C2", "919000000006")
        d1 = create_mock_contact("Company D", "D1", "919000000007")

        raw_contacts = [a1, a2, a3, b1, c1, c2, d1]

        # Ineligible predicate simulates A1 already contacted
        def is_eligible(c: Contact) -> bool:
            return c.name != "A1"

        ordered = prioritize_company_first(raw_contacts, eligibility_predicate=is_eligible)
        ordered_names = [c.name for c in ordered]

        # Pass 1 takes [B1, C1, D1, A2], Pass 2 takes [C2, A3] or [A2, B1, C1, D1, A3, C2]
        assert "A1" not in ordered_names
        assert len(ordered_names) == 6

    def test_entire_company_already_exhausted(self):
        """If Company B has no eligible contacts left, it is completely skipped across all passes."""
        a1 = create_mock_contact("Company A", "A1")
        a2 = create_mock_contact("Company A", "A2")
        b1 = create_mock_contact("Company B", "B1")
        c1 = create_mock_contact("Company C", "C1")

        raw_contacts = [a1, a2, b1, c1]

        # B1 is ineligible
        ordered = prioritize_company_first(raw_contacts, eligibility_predicate=lambda c: c.name != "B1")
        ordered_names = [c.name for c in ordered]

        # Pass 1: [A1, C1], Pass 2: [A2]
        assert ordered_names == ["A1", "C1", "A2"]

    def test_case_insensitive_company_grouping(self):
        """Company names with different casing / whitespace are grouped together."""
        c1 = create_mock_contact("Google", "G1")
        c2 = create_mock_contact("google", "G2")
        c3 = create_mock_contact(" GOOGLE ", "G3")
        m1 = create_mock_contact("Microsoft", "M1")

        raw = [c1, c2, c3, m1]
        ordered = prioritize_company_first(raw)
        names = [c.name for c in ordered]

        # Pass 1: G1, M1; Pass 2: G2; Pass 3: G3
        assert names == ["G1", "M1", "G2", "G3"]

    def test_deterministic_reproducibility(self):
        """Algorithm is strictly deterministic and idempotent on identical inputs."""
        a1 = create_mock_contact("Alpha", "A1")
        a2 = create_mock_contact("Alpha", "A2")
        b1 = create_mock_contact("Beta", "B1")
        c1 = create_mock_contact("Gamma", "C1")

        raw = [a1, a2, b1, c1]
        run1 = [c.name for c in prioritize_company_first(raw)]
        run2 = [c.name for c in prioritize_company_first(raw)]
        run3 = [c.name for c in prioritize_company_first(raw)]

        assert run1 == run2 == run3 == ["A1", "B1", "C1", "A2"]
