"""The cost estimator's inputs and cost items, per workspace

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-30 21:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null until someone saves an estimate; the API answers with the defaults until then.
    op.add_column('eo_settings', sa.Column('cost_estimate', postgresql.JSONB(astext_type=sa.Text()), nullable=True), schema='outreach')


def downgrade() -> None:
    op.drop_column('eo_settings', 'cost_estimate', schema='outreach')
