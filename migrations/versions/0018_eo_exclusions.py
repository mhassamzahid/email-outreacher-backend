"""eo_exclusions: the do-not-contact list, ported from Lead Intel

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-29 15:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('eo_exclusions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('key_type', sa.Text(), nullable=False),
    sa.Column('key_value', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), server_default='do_not_contact', nullable=False),
    sa.Column('business_name', sa.Text(), server_default='', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('added_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("key_type in ('domain', 'email', 'phone', 'place_id', 'name_location')", name=op.f('eo_exclusions_key_type_check')),
    sa.CheckConstraint("reason in ('current_client', 'past_client', 'do_not_contact', 'competitor', 'other')", name=op.f('eo_exclusions_reason_check')),
    sa.ForeignKeyConstraint(['added_by'], ['outreach.eo_users.id'], name=op.f('eo_exclusions_added_by_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('eo_exclusions_pkey')),
    schema='outreach'
    )
    op.create_index('eo_exclusions_key_key', 'eo_exclusions', ['key_type', 'key_value'], unique=True, schema='outreach')


def downgrade() -> None:
    op.drop_index('eo_exclusions_key_key', table_name='eo_exclusions', schema='outreach')
    op.drop_table('eo_exclusions', schema='outreach')
