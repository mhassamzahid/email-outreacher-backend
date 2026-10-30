"""Per ICP: the verification sources research uses, and how many lead posts analysis reads

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-30 22:00:00.000000

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Verification: what research reads to decide whether a business is worth contacting. All on, as before.
    op.add_column('eo_icp_profiles', sa.Column('verification_sources', postgresql.ARRAY(sa.Text()),
                  server_default="{website,jina,instagram,score}", nullable=False), schema='outreach')
    # Lead analysis: posts per source and their age for this ICP's leads; null means the workspace setting.
    op.add_column('eo_icp_profiles', sa.Column('posts_per_source', sa.SmallInteger(), nullable=True), schema='outreach')
    op.add_column('eo_icp_profiles', sa.Column('max_post_age_days', sa.Integer(), nullable=True), schema='outreach')
    op.create_check_constraint(op.f('eo_icp_profiles_posts_per_source_check'), 'eo_icp_profiles',
                               'posts_per_source between 1 and 20', schema='outreach')
    op.create_check_constraint(op.f('eo_icp_profiles_max_post_age_days_check'), 'eo_icp_profiles',
                               'max_post_age_days between 1 and 365', schema='outreach')


def downgrade() -> None:
    op.drop_constraint(op.f('eo_icp_profiles_max_post_age_days_check'), 'eo_icp_profiles', schema='outreach', type_='check')
    op.drop_constraint(op.f('eo_icp_profiles_posts_per_source_check'), 'eo_icp_profiles', schema='outreach', type_='check')
    op.drop_column('eo_icp_profiles', 'max_post_age_days', schema='outreach')
    op.drop_column('eo_icp_profiles', 'posts_per_source', schema='outreach')
    op.drop_column('eo_icp_profiles', 'verification_sources', schema='outreach')
