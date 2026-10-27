"""Request and response models. camelCase on the wire to match the frontend."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """camelCase on the wire to match the frontend's TypeScript types; snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Page[T](ApiModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---------- ICPs ----------

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


# ---------- Companies ----------

class Instagram(ApiModel):
    handle: str | None = None
    url: str | None = None
    followers: int | None = None
    category: str | None = None
    bio: str | None = None


class Company(ApiModel):
    """A Lead Intel business, read-only."""

    id: UUID
    name: str
    city: str | None
    state: str | None
    country: str | None
    icp_id: UUID | None
    icp_name: str | None
    tier: int | None
    problem_score: float | None
    website_status: str | None
    website_url: str | None
    domain: str | None
    instagram: Instagram
    created_at: datetime


class CompanyDetail(Company):
    # Every stored field for the business (website text, Instagram profile, listing data...),
    # exactly as Lead Intel saved it.
    fields: dict[str, str | None]


# ---------- Leads ----------

class LeadIn(ApiModel):
    """One lead added by hand. Needs an email or a LinkedIn URL."""

    icp_id: UUID | None = None
    first_name: str = Field(default="", max_length=200)
    last_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    company: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=300)
    website: str = Field(default="", max_length=500)
    linkedin_url: str = Field(default="", max_length=500)
    instagram_url: str = Field(default="", max_length=500)
    facebook_url: str = Field(default="", max_length=500)
    extra: dict[str, str] = {}


class LeadOut(ApiModel):
    id: UUID
    icp_id: UUID | None = Field(validation_alias="lead_intel_icp_id")
    first_name: str
    last_name: str
    email: str | None
    email_status: str | None
    title: str
    company: str
    website: str
    domain: str | None
    linkedin_url: str | None
    instagram_url: str | None
    facebook_url: str | None
    extra: dict[str, str]
    origin: str
    status: str
    score: int | None
    created_at: datetime


class ColumnMapping(ApiModel):
    column: str
    # The lead field this column fills, or null when it's kept as an extra field.
    field: str | None


class RowOutcome(ApiModel):
    row: int  # spreadsheet row number: the header is row 1
    outcome: Literal["imported", "duplicate", "invalid"]
    reason: str | None = None
    lead_id: UUID | None = None


class ImportResult(ApiModel):
    dry_run: bool
    total_rows: int
    imported: int
    duplicates_in_file: int
    duplicates_existing: int
    invalid: int
    websites_from_email: int
    columns: list[ColumnMapping]
    rows: list[RowOutcome]
