"""Message Template Rotation Policy.

Provides deterministic template distribution mechanisms across campaigns and
batches to support A/B message variants and balanced template allocation.
"""

from __future__ import annotations

from typing import Sequence

from app.domain.message_template import MessageTemplate


def select_template_round_robin(
    templates: Sequence[MessageTemplate],
    index: int,
) -> MessageTemplate:
    """Select template using round-robin indexing."""
    if not templates:
        raise ValueError("Cannot select from empty template list")
    return templates[index % len(templates)]
