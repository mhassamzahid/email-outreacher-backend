"""Each ICP's CheapSequencer campaign

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-30 17:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One CheapSequencer account serves every workspace, so the link is per ICP. It survives renames.
    op.create_table('eo_icp_campaigns',
    sa.Column('lead_intel_icp_id', sa.Uuid(), nullable=False),
    sa.Column('campaign_id', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('lead_intel_icp_id', name=op.f('eo_icp_campaigns_pkey')),
    schema='outreach'
    )


def downgrade() -> None:
    op.drop_table('eo_icp_campaigns', schema='outreach')
