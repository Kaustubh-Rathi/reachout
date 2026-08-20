"""Message Template Rotation Policy.

Provides deterministic template distribution mechanisms across campaigns and
batches to support A/B message variants and balanced template allocation.
"""

from __future__ import annotations

import hashlib
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


def select_template_deterministic(
    templates: Sequence[MessageTemplate],
    seed_key: str,
) -> MessageTemplate:
    """Deterministically select a template based on contact ID or company ID hash.

    Ensures the same recipient consistently maps to the same template variant
    across multiple simulation/dry runs.
    """
    if not templates:
        raise ValueError("Cannot select from empty template list")
    digest = hashlib.md5(seed_key.encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(templates)
    return templates[idx]
