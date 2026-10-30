"""Sending through CheapSequencer: email steps and queue state, follow-up prompts, sending settings, ICP campaigns

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-30 12:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

FALLBACK_2 = "Hi {{first_name}}, just bringing this back to the top of your inbox in case it got buried. Worth a look?"
FALLBACK_3 = "Hi {{first_name}}, I'll leave it here so I'm not cluttering your inbox. If it's ever useful, just reply and I'll send the details."


def upgrade() -> None:
    # Emails: which of the 3 steps, a "queued" status, and where the sequence stands in CheapSequencer.
    op.add_column('eo_emails', sa.Column('step', sa.SmallInteger(), server_default='1', nullable=False), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequencer_campaign_id', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_status', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_sent_count', sa.SmallInteger(), server_default='0', nullable=False), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_last_sent_at', sa.DateTime(timezone=True), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_stop_reason', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_synced_at', sa.DateTime(timezone=True), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequence_raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True), schema='outreach')
    op.create_check_constraint(op.f('eo_emails_step_check'), 'eo_emails', 'step between 1 and 3', schema='outreach')
    op.drop_constraint(op.f('eo_emails_status_check'), 'eo_emails', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_emails_status_check'), 'eo_emails', "status in ('draft', 'edited', 'approved', 'queued', 'sent', 'skipped')", schema='outreach'
    )
    op.create_index('eo_emails_sequence_idx', 'eo_emails', ['workspace_id', 'sequence_status'], schema='outreach',
                    postgresql_where=sa.text('sequence_status is not null'))

    # Follow-up prompts: email 2 and email 3.
    op.drop_constraint(op.f('eo_prompts_kind_check'), 'eo_prompts', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_prompts_kind_check'), 'eo_prompts', "kind in ('verify', 'hook', 'email', 'score', 'followup2', 'followup3')", schema='outreach'
    )

    # Sending settings.
    op.add_column('eo_settings', sa.Column('sender_company', sa.Text(), server_default='', nullable=False), schema='outreach')
    op.add_column('eo_settings', sa.Column('sender_address', sa.Text(), server_default='', nullable=False), schema='outreach')
    op.add_column('eo_settings', sa.Column('followup_fallback_2', sa.Text(), server_default=FALLBACK_2, nullable=False), schema='outreach')
    op.add_column('eo_settings', sa.Column('followup_fallback_3', sa.Text(), server_default=FALLBACK_3, nullable=False), schema='outreach')
    op.add_column('eo_settings', sa.Column('followup_lead_hours', sa.SmallInteger(), server_default='24', nullable=False), schema='outreach')
    op.create_check_constraint(op.f('eo_settings_followup_lead_hours_check'), 'eo_settings', 'followup_lead_hours between 2 and 72', schema='outreach')

    # Which CheapSequencer campaign an ICP's emails go into.
    op.add_column('eo_icp_profiles', sa.Column('sequencer_campaign_id', sa.Text(), nullable=True), schema='outreach')


def downgrade() -> None:
    op.drop_column('eo_icp_profiles', 'sequencer_campaign_id', schema='outreach')
    op.drop_constraint(op.f('eo_settings_followup_lead_hours_check'), 'eo_settings', schema='outreach', type_='check')
    for c in ('followup_lead_hours', 'followup_fallback_3', 'followup_fallback_2', 'sender_address', 'sender_company'):
        op.drop_column('eo_settings', c, schema='outreach')
    op.execute("delete from outreach.eo_prompts where kind in ('followup2', 'followup3')")
    op.drop_constraint(op.f('eo_prompts_kind_check'), 'eo_prompts', schema='outreach', type_='check')
    op.create_check_constraint(op.f('eo_prompts_kind_check'), 'eo_prompts', "kind in ('verify', 'hook', 'email', 'score')", schema='outreach')
    op.drop_index('eo_emails_sequence_idx', table_name='eo_emails', schema='outreach')
    op.execute("delete from outreach.eo_emails where step > 1")
    op.execute("update outreach.eo_emails set status = 'approved' where status = 'queued'")
    op.drop_constraint(op.f('eo_emails_status_check'), 'eo_emails', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_emails_status_check'), 'eo_emails', "status in ('draft', 'edited', 'approved', 'sent', 'skipped')", schema='outreach'
    )
    op.drop_constraint(op.f('eo_emails_step_check'), 'eo_emails', schema='outreach', type_='check')
    for c in ('sequence_raw', 'sequence_synced_at', 'sequence_stop_reason', 'sequence_last_sent_at', 'sequence_sent_count',
              'sequence_status', 'queued_at', 'sequencer_campaign_id', 'step'):
        op.drop_column('eo_emails', c, schema='outreach')
