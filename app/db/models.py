"""Tables owned by this service. All of them live in the `outreach` schema.

Lead Intel ids (ICPs, businesses) are stored as plain uuids with no foreign key: a foreign key
would make Lead Intel's own deletes and merges fail, which is a change to its tables in effect.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, DateTime, MetaData, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

OUTREACH_SCHEMA = "outreach"


class Base(DeclarativeBase):
    metadata = MetaData(schema=OUTREACH_SCHEMA)


class IcpProfile(Base):
    """What the outreach side needs to know about a Lead Intel ICP to write emails for it."""

    __tablename__ = "icp_profiles"

    lead_intel_icp_id: Mapped[UUID] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Stakeholder titles to look for at each company (used by the Apollo search).
    titles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    company_size: Mapped[str] = mapped_column(Text, default="")
    pains: Mapped[str] = mapped_column(Text, default="")
    offer: Mapped[str] = mapped_column(Text, default="")
    proof: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(Text, default="")
    tone: Mapped[str] = mapped_column(Text, default="")
    example_email: Mapped[str] = mapped_column(Text, default="")
    words_to_avoid: Mapped[str] = mapped_column(Text, default="")
    hook_guidance: Mapped[str] = mapped_column(Text, default="")
    enabled_sources: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    hook_prompt_id: Mapped[UUID | None] = mapped_column(default=None)
    email_prompt_id: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
