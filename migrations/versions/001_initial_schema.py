"""Initial Reachout Relational Schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-08-17 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. companies
    op.create_table(
        'companies',
        sa.Column('id', sa.String(length=128), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('normalized_name', sa.String(length=255), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_companies_normalized_name', 'companies', ['normalized_name'], unique=False)

    # 2. contacts
    op.create_table(
        'contacts',
        sa.Column('contact_id', sa.String(length=64), nullable=False),
        sa.Column('company_id', sa.String(length=128), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('designation', sa.String(length=255), server_default='', nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_whatsapp_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_email_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('crm_outcome', sa.String(length=32), server_default='NONE', nullable=False),
        sa.Column('interview_status', sa.String(length=32), server_default='NOT_APPLICABLE', nullable=False),
        sa.Column('interested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('interview_status_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), server_default='', nullable=False),
        sa.Column('tags_json', sa.Text(), server_default='[]', nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('contact_id'),
        sa.CheckConstraint('phone IS NOT NULL OR email IS NOT NULL', name='ck_contacts_reachable')
    )
    op.create_index('ix_contacts_company_id', 'contacts', ['company_id'], unique=False)
    op.create_index('ix_contacts_phone', 'contacts', ['phone'], unique=False)
    op.create_index('ix_contacts_email', 'contacts', ['email'], unique=False)
    op.create_index('ix_contacts_company_phone', 'contacts', ['company_id', 'phone'], unique=False)
    op.create_index('ix_contacts_company_email', 'contacts', ['company_id', 'email'], unique=False)

    # 3. source_records
    op.create_table(
        'source_records',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('contact_id', sa.String(length=64), nullable=False),
        sa.Column('source_file', sa.String(length=255), nullable=False),
        sa.Column('source_sheet', sa.String(length=128), nullable=True),
        sa.Column('source_row', sa.Integer(), nullable=False),
        sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('raw_payload_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.contact_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_source_records_contact_id', 'source_records', ['contact_id'], unique=False)
    op.create_index('ix_source_records_source_fingerprint', 'source_records', ['source_fingerprint'], unique=False)
    op.create_index('ix_source_file_row', 'source_records', ['source_file', 'source_row'], unique=False)

    # 4. sender_accounts
    op.create_table(
        'sender_accounts',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('identity', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='ACTIVE', nullable=False),
        sa.Column('credential_ref', sa.String(length=255), nullable=True),
        sa.Column('session_ref', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('daily_limit', sa.Integer(), nullable=True),
        sa.Column('hourly_limit', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel', 'identity', name='uq_sender_channel_identity')
    )
    op.create_index('ix_sender_accounts_channel', 'sender_accounts', ['channel'], unique=False)

    # 5. campaigns
    op.create_table(
        'campaigns',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='IDLE', nullable=False),
        sa.Column('template_ids_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('sender_account_ids_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_json', sa.Text(), server_default='{}', nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 6. message_templates
    op.create_table(
        'message_templates',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('attachment_ref', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 7. outreach_attempts
    op.create_table(
        'outreach_attempts',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('contact_id', sa.String(length=64), nullable=False),
        sa.Column('sender_account_id', sa.String(length=64), nullable=True),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('attempt_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('message_body_snapshot', sa.Text(), nullable=False),
        sa.Column('campaign_id', sa.String(length=64), nullable=True),
        sa.Column('template_id', sa.String(length=64), nullable=True),
        sa.Column('subject_snapshot', sa.String(length=500), nullable=True),
        sa.Column('attachment_snapshot', sa.String(length=500), nullable=True),
        sa.Column('prepared_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_code', sa.String(length=64), nullable=True),
        sa.Column('failure_detail', sa.Text(), nullable=True),
        sa.Column('provider_reference', sa.String(length=255), nullable=True),
        sa.Column('recovery_notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.contact_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_account_id'], ['sender_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['template_id'], ['message_templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_outreach_attempts_contact_id', 'outreach_attempts', ['contact_id'], unique=False)
    op.create_index('ix_outreach_attempts_sender_account_id', 'outreach_attempts', ['sender_account_id'], unique=False)
    op.create_index('ix_outreach_attempts_status', 'outreach_attempts', ['status'], unique=False)
    op.create_index('ix_outreach_attempts_campaign_id', 'outreach_attempts', ['campaign_id'], unique=False)
    op.create_index('ix_outreach_attempts_idempotency_key', 'outreach_attempts', ['idempotency_key'], unique=True)

    # 8. follow_up_reminders
    op.create_table(
        'follow_up_reminders',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('contact_id', sa.String(length=64), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='PENDING', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.contact_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_follow_up_reminders_contact_id', 'follow_up_reminders', ['contact_id'], unique=False)
    op.create_index('ix_follow_up_reminders_due_at', 'follow_up_reminders', ['due_at'], unique=False)

    # 9. crm_events
    op.create_table(
        'crm_events',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('contact_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('previous_state_json', sa.Text(), nullable=True),
        sa.Column('new_state_json', sa.Text(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor', sa.String(length=64), server_default='system', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.contact_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_crm_events_contact_id', 'crm_events', ['contact_id'], unique=False)

    # 10. suppression_records
    op.create_table(
        'suppression_records',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('suppression_type', sa.String(length=32), nullable=False),
        sa.Column('identifier', sa.String(length=255), nullable=False),
        sa.Column('reason', sa.String(length=500), server_default='MANUAL_CRM_DELETION', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('suppression_type', 'identifier', name='uq_suppression_type_identifier')
    )
    op.create_index('ix_suppression_records_identifier', 'suppression_records', ['identifier'], unique=False)


def downgrade() -> None:
    op.drop_table('suppression_records')
    op.drop_table('crm_events')
    op.drop_table('follow_up_reminders')
    op.drop_table('outreach_attempts')
    op.drop_table('message_templates')
    op.drop_table('campaigns')
    op.drop_table('sender_accounts')
    op.drop_table('source_records')
    op.drop_table('contacts')
    op.drop_table('companies')
