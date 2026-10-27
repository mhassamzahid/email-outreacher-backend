"""outreach.icp_profiles

Revision ID: 0001
Revises:
Create Date: 2026-10-27

Only objects in the `outreach` schema may be created or changed here. Never Lead Intel's tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create schema if not exists outreach")
    op.create_table(
        "icp_profiles",
        # Lead Intel's icps.id. Deliberately no foreign key: see app/db/models.py.
        sa.Column("lead_intel_icp_id", sa.Uuid(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("titles", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("company_size", sa.Text(), nullable=False, server_default=""),
        sa.Column("pains", sa.Text(), nullable=False, server_default=""),
        sa.Column("offer", sa.Text(), nullable=False, server_default=""),
        sa.Column("proof", sa.Text(), nullable=False, server_default=""),
        sa.Column("cta", sa.Text(), nullable=False, server_default=""),
        sa.Column("tone", sa.Text(), nullable=False, server_default=""),
        sa.Column("example_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("words_to_avoid", sa.Text(), nullable=False, server_default=""),
        sa.Column("hook_guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled_sources", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("hook_prompt_id", sa.Uuid(), nullable=True),
        sa.Column("email_prompt_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="outreach",
    )


def downgrade() -> None:
    op.drop_table("icp_profiles", schema="outreach")
