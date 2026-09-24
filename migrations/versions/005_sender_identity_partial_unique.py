"""Reconcile sender identity uniqueness with the ORM schema

Revision ID: 005_sender_identity_partial_unique
Revises: 004_remap_legacy_stopped_campaigns
Create Date: 2026-09-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_sender_identity_partial_unique"
down_revision: Union[str, None] = "004_remap_legacy_stopped_campaigns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _duplicate_identities() -> list[tuple[str, str, int]]:
    return [
        (row[0], row[1], row[2])
        for row in op.get_bind()
        .execute(
            sa.text(
                "SELECT channel, identity, COUNT(*) FROM sender_accounts "
                "WHERE identity != '' GROUP BY channel, identity HAVING COUNT(*) > 1"
            )
        )
        .fetchall()
    ]


def upgrade() -> None:
    duplicates = _duplicate_identities()
    if duplicates:
        raise RuntimeError(f"Cannot create sender identity index with duplicate identities: {duplicates}")

    with op.batch_alter_table("sender_accounts", schema=None) as batch_op:
        batch_op.drop_constraint("uq_sender_channel_identity", type_="unique")
        batch_op.create_index(
            "uq_sender_channel_identity",
            ["channel", "identity"],
            unique=True,
            sqlite_where=sa.text("identity != ''"),
        )


def downgrade() -> None:
    placeholders = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM sender_accounts WHERE identity = ''")).scalar()
    if placeholders > 1:
        raise RuntimeError("Cannot downgrade sender identity uniqueness with repeated empty identities")

    with op.batch_alter_table("sender_accounts", schema=None) as batch_op:
        batch_op.drop_index("uq_sender_channel_identity")
        batch_op.create_unique_constraint("uq_sender_channel_identity", ["channel", "identity"])
