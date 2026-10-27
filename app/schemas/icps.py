from datetime import datetime
from uuid import UUID

from app.schemas.base import ApiModel


class IcpProfileIn(ApiModel):
    description: str = ""
    titles: list[str] = []
    company_size: str = ""
    pains: str = ""
    offer: str = ""
    proof: str = ""
    cta: str = ""
    tone: str = ""
    example_email: str = ""
    words_to_avoid: str = ""
    hook_guidance: str = ""
    enabled_sources: list[str] = []
    hook_prompt_id: UUID | None = None
    email_prompt_id: UUID | None = None


class IcpProfileOut(IcpProfileIn):
    updated_at: datetime


class Icp(ApiModel):
    """A Lead Intel ICP (read-only) with this service's outreach profile for it, if one exists."""

    id: UUID
    name: str
    industry: str
    priority: int
    enabled: bool
    search_terms: list[str]
    places: list[str]
    company_count: int
    created_at: datetime
    updated_at: datetime
    profile: IcpProfileOut | None = None
