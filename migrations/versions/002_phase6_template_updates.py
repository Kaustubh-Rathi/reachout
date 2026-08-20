"""Phase 6 Template Fields and Policy Updates

Revision ID: 002_phase6_template_updates
Revises: 001_initial_schema
Create Date: 2026-08-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_phase6_template_updates'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add phone_number and active columns to message_templates
    with op.batch_alter_table('message_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone_number', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('active', sa.Boolean(), server_default='1', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('message_templates', schema=None) as batch_op:
        batch_op.drop_column('active')
        batch_op.drop_column('phone_number')
