"""Lead Intel's tables, described for reading only.

These tables belong to Lead Intel and must never be written, altered or migrated from here.
They live in their own MetaData, which Alembic never sees, and every query against them runs in a
READ ONLY transaction (see app/db/session.py). Only the columns this service reads are listed.
"""

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

lead_intel_metadata = MetaData(schema="public")

icps = Table(
    "icps",
    lead_intel_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text),
    Column("industry", Text),
    Column("priority", Integer),
    Column("enabled", Boolean),
    Column("search_terms", ARRAY(Text)),
    Column("places", ARRAY(Text)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

businesses = Table(
    "businesses",
    lead_intel_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", Text),
    Column("display_name", Text),
    Column("website_status", Text),
    Column("city", Text),
    Column("state", Text),
    Column("country", Text),
    Column("exclusion_flag", Boolean),
    Column("problem_score", Float),
    Column("tier", SmallInteger),
    Column("icp_id", UUID(as_uuid=True)),
    Column("icp_name", Text),
    Column("created_at", DateTime(timezone=True)),
)

field_metadata = Table(
    "field_metadata",
    lead_intel_metadata,
    Column("business_id", UUID(as_uuid=True)),
    Column("field_name", Text),
    Column("value", Text),
    Column("source_name", Text),
    Column("last_verified_at", DateTime(timezone=True)),
)

identity_keys = Table(
    "identity_keys",
    lead_intel_metadata,
    Column("business_id", UUID(as_uuid=True)),
    Column("key_type", Text),
    Column("key_value", Text),
)
