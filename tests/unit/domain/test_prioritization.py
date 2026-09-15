"""Unit tests for Company-First Prioritization Policy."""

from app.domain.contact import Contact
from app.domain.policies.prioritization import prioritize_company_first


class TestCompanyFirstPrioritization:
    def test_interleaving_pattern_company_first(self):
        """Verify the exact sequence: A1, B1, C1, D1, A2, C2, A3."""
        contacts = [
            Contact(contact_id="A1", company_id="CompanyA", name="Alice 1"),
            Contact(contact_id="A2", company_id="CompanyA", name="Alice 2"),
            Contact(contact_id="A3", company_id="CompanyA", name="Alice 3"),
            Contact(contact_id="B1", company_id="CompanyB", name="Bob 1"),
            Contact(contact_id="C1", company_id="CompanyC", name="Charlie 1"),
            Contact(contact_id="C2", company_id="CompanyC", name="Charlie 2"),
            Contact(contact_id="D1", company_id="CompanyD", name="David 1"),
        ]

        prioritized = prioritize_company_first(contacts)
        result_ids = [c.contact_id for c in prioritized]

        expected_ids = ["A1", "B1", "C1", "D1", "A2", "C2", "A3"]
        assert result_ids == expected_ids

    def test_prioritization_with_eligibility_filter(self):
        """Verify ineligible contacts are excluded before round-robin distribution."""
        contacts = [
            Contact(contact_id="A1", company_id="CoA", name="A1", phone="919999999991"),
            Contact(contact_id="A2", company_id="CoA", name="A2", phone=None),  # Ineligible
            Contact(contact_id="B1", company_id="CoB", name="B1", phone="919999999992"),
            Contact(contact_id="B2", company_id="CoB", name="B2", phone="919999999993"),
        ]

        # Only allow contacts with phone
        prioritized = prioritize_company_first(
            contacts,
            eligibility_predicate=lambda c: bool(c.phone),
        )
        result_ids = [c.contact_id for c in prioritized]

        assert result_ids == ["A1", "B1", "B2"]

    def test_single_company_all_contacts(self):
        """When only one company exists, contacts preserve their arrival order."""
        contacts = [
            Contact(contact_id="A1", company_id="CoA", name="A1"),
            Contact(contact_id="A2", company_id="CoA", name="A2"),
            Contact(contact_id="A3", company_id="CoA", name="A3"),
        ]
        prioritized = prioritize_company_first(contacts)
        assert [c.contact_id for c in prioritized] == ["A1", "A2", "A3"]

    def test_empty_contacts_list(self):
        assert prioritize_company_first([]) == []
