"""eo_companies: companies found on Google Maps (Places API), with their place id and phone

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-29 10:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('eo_companies', sa.Column('google_place_id', sa.Text(), nullable=True), schema='outreach')
    op.add_column('eo_companies', sa.Column('phone', sa.Text(), nullable=True), schema='outreach')
    op.drop_constraint(op.f('eo_companies_source_check'), 'eo_companies', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_companies_source_check'), 'eo_companies', "source in ('lead_intel', 'apollo', 'google_maps')", schema='outreach'
    )
    op.create_index(
        'eo_companies_workspace_place_key', 'eo_companies', ['workspace_id', 'google_place_id'], unique=True,
        schema='outreach', postgresql_where=sa.text('google_place_id is not null'),
    )


def downgrade() -> None:
    op.drop_index('eo_companies_workspace_place_key', table_name='eo_companies', schema='outreach')
    op.execute("delete from outreach.eo_companies where source = 'google_maps'")
    op.drop_constraint(op.f('eo_companies_source_check'), 'eo_companies', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_companies_source_check'), 'eo_companies', "source in ('lead_intel', 'apollo')", schema='outreach'
    )
    op.drop_column('eo_companies', 'phone', schema='outreach')
    op.drop_column('eo_companies', 'google_place_id', schema='outreach')
