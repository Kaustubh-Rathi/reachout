"""Unit tests for Company domain entity."""

from app.domain.company import Company, normalize_company_name


class TestCompanyModel:
    def test_company_normalization(self):
        assert normalize_company_name("  Google   LLC  ") == "google llc"
        assert normalize_company_name("Amazon Web Services") == "amazon web services"
        assert normalize_company_name("") == ""

    def test_company_creation(self):
        comp = Company.create(
            name=" Stripe, Inc. ",
            domain="stripe.com",
        )
        assert comp.name == "Stripe, Inc."
        assert comp.normalized_name == "stripe, inc."
        assert comp.domain == "stripe.com"
        assert comp.id == "stripe-inc"

    def test_explicit_id_preservation(self):
        comp = Company.create(
            name="OpenAI",
            company_id="custom_comp_001",
        )
        assert comp.id == "custom_comp_001"
        assert comp.name == "OpenAI"
