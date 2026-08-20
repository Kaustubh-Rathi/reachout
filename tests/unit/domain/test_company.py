"""Unit tests for Company domain entity."""

from datetime import datetime, timezone
import pytest

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

    def test_update_name(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        comp = Company.create(name="Facebook", created_at=t0)
        t1 = datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
        comp.update_name("Meta Platforms", timestamp=t1)
        assert comp.name == "Meta Platforms"
        assert comp.normalized_name == "meta platforms"
        assert comp.updated_at == t1
