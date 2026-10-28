"""eo_provider_calls: action, model and tokens, for the usage page

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('eo_provider_calls', sa.Column('action', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_provider_calls', sa.Column('model', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_provider_calls', sa.Column('input_tokens', sa.Integer(), nullable=True), schema='outreach')
    op.add_column('eo_provider_calls', sa.Column('output_tokens', sa.Integer(), nullable=True), schema='outreach')


def downgrade() -> None:
    op.drop_column('eo_provider_calls', 'output_tokens', schema='outreach')
    op.drop_column('eo_provider_calls', 'input_tokens', schema='outreach')
    op.drop_column('eo_provider_calls', 'model', schema='outreach')
    op.drop_column('eo_provider_calls', 'action', schema='outreach')
