"""Phase 7 Multi-Channel Endpoint Coverage and Outreach Destination

Revision ID: 003_phase7_endpoint_coverage
Revises: 002_phase6_template_updates
Create Date: 2026-08-19 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_phase7_endpoint_coverage'
down_revision: Union[str, None] = '002_phase6_template_updates'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add destination column and index to outreach_attempts
    with op.batch_alter_table('outreach_attempts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('destination', sa.String(length=255), nullable=True))
        batch_op.create_index('ix_outreach_attempts_destination', ['destination'])


def downgrade() -> None:
    with op.batch_alter_table('outreach_attempts', schema=None) as batch_op:
        batch_op.drop_index('ix_outreach_attempts_destination')
        batch_op.drop_column('destination')
