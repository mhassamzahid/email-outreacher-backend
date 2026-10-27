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
    # When this company was pulled into the workspace in X-Workspace-Id; null if it isn't in it.
    imported_at: datetime | None = None


class CompanyDetail(Company):
    # Every stored field for the business (website text, Instagram profile, listing data...),
    # exactly as Lead Intel saved it.
    fields: dict[str, str | None]


class CompanyImportIn(ApiModel):
    """Either specific Lead Intel companies, or every contactable company in one Lead Intel ICP."""

    company_ids: list[UUID] | None = Field(default=None, max_length=5000)
    icp_id: UUID | None = None


class CompanyImportResult(ApiModel):
    imported: int
    already_in_workspace: int
    # Ids Lead Intel doesn't have, or that are excluded or merged and so can't be contacted.
    not_found: list[UUID]


class CompanyRemoveIn(ApiModel):
    company_ids: list[UUID] = Field(min_length=1, max_length=5000)


class CompanyRemoveResult(ApiModel):
    removed: int


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


# ---------- Workspaces ----------


class WorkspaceIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class WorkspaceUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class WorkspaceOut(ApiModel):
    id: UUID
    name: str
    description: str
    created_at: datetime


# ---------- Stakeholders (Apollo) ----------


class StakeholderOut(ApiModel):
    id: UUID
    company_id: UUID | None = Field(validation_alias="lead_intel_business_id")
    source: str
    first_name: str
    # Masked ("Hu***n") until the person is revealed.
    last_name: str
    title: str
    seniority: str | None
    linkedin_url: str | None
    has_email: bool | None
    email: str | None
    email_status: str | None
    revealed_at: datetime | None
    lead_id: UUID | None
    found_at: datetime


class StakeholderSearchIn(ApiModel):
    company_ids: list[UUID] = Field(min_length=1, max_length=25)
    titles: list[str] = Field(default=[], max_length=30)
    seniorities: list[str] = Field(default=[], max_length=11)


class CompanySearchResult(ApiModel):
    company_id: UUID
    name: str | None
    status: Literal["searched", "skipped", "failed"]
    found: int = 0
    new: int = 0
    # How many people Apollo has in total for this search; we save the first page.
    total_at_apollo: int | None = None
    reason: str | None = None


class StakeholderSearchResult(ApiModel):
    results: list[CompanySearchResult]
    # Set when Apollo refused (bad key, plan, rate limit); the rest weren't searched.
    error: str | None = None


class StakeholderRevealIn(ApiModel):
    stakeholder_ids: list[UUID] = Field(min_length=1, max_length=25)


class RevealOutcome(ApiModel):
    stakeholder_id: UUID
    status: Literal["revealed", "no_match", "already_revealed", "not_found", "failed"]
    email: str | None = None
    lead_id: UUID | None = None
    lead_created: bool = False
    reason: str | None = None


class StakeholderRevealResult(ApiModel):
    outcomes: list[RevealOutcome]
    revealed: int
    error: str | None = None
