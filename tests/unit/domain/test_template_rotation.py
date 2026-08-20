"""Unit tests for Template Rotation and Distribution Policy."""

import pytest

from app.domain.enums import Channel
from app.domain.message_template import MessageTemplate
from app.domain.policies.template_rotation import (
    select_template_deterministic,
    select_template_round_robin,
)


class TestTemplateRotation:
    @pytest.fixture
    def sample_templates(self):
        return [
            MessageTemplate.create(name="T1", channel=Channel.WHATSAPP, body="Template 1", template_id="t1"),
            MessageTemplate.create(name="T2", channel=Channel.WHATSAPP, body="Template 2", template_id="t2"),
            MessageTemplate.create(name="T3", channel=Channel.WHATSAPP, body="Template 3", template_id="t3"),
        ]

    def test_round_robin_selection(self, sample_templates):
        t0 = select_template_round_robin(sample_templates, 0)
        t1 = select_template_round_robin(sample_templates, 1)
        t2 = select_template_round_robin(sample_templates, 2)
        t3 = select_template_round_robin(sample_templates, 3)

        assert t0.id == "t1"
        assert t1.id == "t2"
        assert t2.id == "t3"
        assert t3.id == "t1"  # Cycles back

    def test_deterministic_seed_selection(self, sample_templates):
        chosen_a1 = select_template_deterministic(sample_templates, "contact_abc")
        chosen_a2 = select_template_deterministic(sample_templates, "contact_abc")
        assert chosen_a1.id == chosen_a2.id

        # Verify it selects a valid template in the pool
        assert chosen_a1 in sample_templates

    def test_empty_template_list_raises(self):
        with pytest.raises(ValueError, match="Cannot select from empty"):
            select_template_round_robin([], 0)

        with pytest.raises(ValueError, match="Cannot select from empty"):
            select_template_deterministic([], "seed")
