"""The mailbox each lead's sequence sends from

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-30 15:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CheapSequencer assigns each lead one mailbox and sends all three emails from it.
    op.add_column('eo_emails', sa.Column('sequencer_inbox_id', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_emails', sa.Column('sequencer_inbox_email', sa.Text(), nullable=True), schema='outreach')


def downgrade() -> None:
    op.drop_column('eo_emails', 'sequencer_inbox_email', schema='outreach')
    op.drop_column('eo_emails', 'sequencer_inbox_id', schema='outreach')
