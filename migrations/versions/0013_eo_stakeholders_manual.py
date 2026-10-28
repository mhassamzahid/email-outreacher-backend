"""eo_stakeholders: people added by hand or from a CSV (source 'manual')

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28 20:00:00.000000
"""

from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f('eo_stakeholders_source_check'), 'eo_stakeholders', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_stakeholders_source_check'), 'eo_stakeholders', "source in ('apollo', 'apify_linkedin', 'manual')",
        schema='outreach',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('eo_stakeholders_source_check'), 'eo_stakeholders', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_stakeholders_source_check'), 'eo_stakeholders', "source in ('apollo', 'apify_linkedin')",
        schema='outreach',
    )
