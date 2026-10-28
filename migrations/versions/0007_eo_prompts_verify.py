"""Company Verification prompts: a `verify` prompt kind, and the ICP profile's choice of one

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-29 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f('eo_prompts_kind_check'), 'eo_prompts', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_prompts_kind_check'), 'eo_prompts', "kind in ('verify', 'hook', 'email', 'score')", schema='outreach'
    )
    op.add_column('eo_icp_profiles', sa.Column('verify_prompt_id', sa.Uuid(), nullable=True), schema='outreach')
    op.create_foreign_key(
        op.f('eo_icp_profiles_verify_prompt_id_fkey'), 'eo_icp_profiles', 'eo_prompts', ['verify_prompt_id'], ['id'],
        source_schema='outreach', referent_schema='outreach', ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('eo_icp_profiles_verify_prompt_id_fkey'), 'eo_icp_profiles', schema='outreach', type_='foreignkey')
    op.drop_column('eo_icp_profiles', 'verify_prompt_id', schema='outreach')
    op.drop_constraint(op.f('eo_prompts_kind_check'), 'eo_prompts', schema='outreach', type_='check')
    op.create_check_constraint(
        op.f('eo_prompts_kind_check'), 'eo_prompts', "kind in ('hook', 'email', 'score')", schema='outreach'
    )
