"""Remap legacy stopped campaigns to paused

Revision ID: 004_remap_legacy_stopped_campaigns
Revises: 003_phase7_endpoint_coverage
Create Date: 2026-09-22 00:00:00.000000

Data migration for the stop-operation removal: legacy STOPPED / STOPPING
campaign rows are remapped to PAUSED, preserving the pre-image status in
metadata_json (remapped_from) with an explanatory remap_note.

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004_remap_legacy_stopped_campaigns"
down_revision: Union[str, None] = "003_phase7_endpoint_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, status, metadata_json FROM campaigns WHERE status IN ('STOPPED', 'STOPPING')")
    ).fetchall()
    for row in rows:
        campaign_id = row[0]
        old_status = row[1]
        metadata_json = row[2] if len(row) > 2 else None
        try:
            meta = json.loads(metadata_json) if metadata_json else {}
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("remapped_from", old_status)
        meta.setdefault(
            "remap_note",
            f"Legacy {old_status} status remapped to PAUSED during stop-operation removal (004)",
        )
        conn.execute(
            sa.text("UPDATE campaigns SET status = 'PAUSED', metadata_json = :metadata_json WHERE id = :id"),
            {"metadata_json": json.dumps(meta), "id": campaign_id},
        )


def downgrade() -> None:
    # No-op: the pre-image (STOPPED vs STOPPING) is unrecoverable from PAUSED
    # alone (remapped_from is informational), and the STOPPING/STOPPED enum
    # members no longer exist, so a status downgrade cannot be expressed.
    pass
