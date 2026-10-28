"""Request and response models. camelCase on the wire to match the frontend."""

from datetime import datetime
from typing import Any, Literal
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
    """This workspace's outreach settings for a Lead Intel ICP. The ICP itself stays in Lead Intel."""

    description: str = ""
    titles: list[str] = []
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


ICP_SOURCES = (
    "business_name", "website_text", "website_domain", "instagram_bio", "instagram_category", "instagram_handle",
    "meta_ads_text",
)  # fmt: skip


class IcpCreateIn(ApiModel):
    """A new ICP, with exactly Lead Intel's fields. Added to Lead Intel's icps table."""

    name: str = Field(min_length=1, max_length=100)
    industry: str = Field(min_length=1, max_length=100)
    priority: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True
    sources: list[str] = Field(default=[], max_length=len(ICP_SOURCES))
    any_of: list[str] = Field(default=[], max_length=100)
    none_of: list[str] = Field(default=[], max_length=100)
    website_any_of: list[str] = Field(default=[], max_length=100)
    instagram_any_of: list[str] = Field(default=[], max_length=100)
    require_all: bool = False
    min_followers: int | None = Field(default=None, ge=0)
    max_followers: int | None = Field(default=None, ge=0)
    search_terms: list[str] = Field(default=[], max_length=50)
    requires_website: bool = False
    min_reviews: int | None = Field(default=None, ge=0)
    places: list[str] = Field(default=[], max_length=100)


class Icp(ApiModel):
    """A Lead Intel ICP, every field as Lead Intel stores it (read-only), plus this workspace's outreach
    profile for it if one exists."""

    id: UUID
    name: str
    industry: str
    priority: int
    enabled: bool
    # Which text the rule reads; empty means all of it.
    sources: list[str]
    any_of: list[str]
    none_of: list[str]
    website_any_of: list[str]
    instagram_any_of: list[str]
    require_all: bool
    min_followers: int | None
    max_followers: int | None
    # What Lead Intel searches Google Maps with.
    search_terms: list[str]
    requires_website: bool
    min_reviews: int | None
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
    # The pipeline company (eo_companies) they were found at.
    pipeline_company_id: UUID | None = Field(default=None, validation_alias="company_id")
    icp_id: UUID | None = Field(validation_alias="lead_intel_icp_id")
    organization_name: str | None
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
    # Set when the provider refused (bad key, plan, rate limit, credit); the rest weren't searched.
    error: str | None = None
    # What the provider charged, when it says (Apify does; Apollo searches are free).
    cost_usd: float | None = None


class LinkedInSearchIn(ApiModel):
    """Find people at companies on LinkedIn (Apify). Paid per profile; no emails."""

    company_ids: list[UUID] = Field(min_length=1, max_length=10)
    titles: list[str] = Field(default=[], max_length=30)
    max_per_company: int = Field(default=10, ge=1, le=50)


class AddAsLeadsIn(ApiModel):
    stakeholder_ids: list[UUID] = Field(min_length=1, max_length=100)


class AddAsLeadsResult(ApiModel):
    added: int
    # Already a lead (same email or LinkedIn), now linked to it.
    linked_existing: int
    skipped: int


class IcpSearchIn(ApiModel):
    """Search Apollo for people who fit a Lead Intel ICP, anywhere, not just at companies already imported."""

    icp_id: UUID
    titles: list[str] = Field(default=[], max_length=30)
    seniorities: list[str] = Field(default=[], max_length=11)
    # Company headquarters: cities, states, countries or postcodes.
    locations: list[str] = Field(default=[], max_length=30)
    keywords: str = Field(default="", max_length=200)
    # "min,max" employee counts, e.g. "1,10".
    employee_ranges: list[str] = Field(default=[], max_length=10)
    verified_email_only: bool = True
    per_page: int = Field(default=25, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=500)


class IcpSearchResult(ApiModel):
    found: int
    new: int
    total_at_apollo: int | None
    page: int
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


# ---------- Posts ----------


class PostFetchIn(ApiModel):
    lead_ids: list[UUID] = Field(min_length=1, max_length=25)
    max_posts: int = Field(default=5, ge=1, le=20)
    max_age_days: int = Field(default=60, ge=1, le=365)


class LeadPostsResult(ApiModel):
    lead_id: UUID
    # Posts found per source; null when the lead has no URL for it.
    linkedin: int | None
    instagram: int | None
    new: int


class PostFetchResult(ApiModel):
    results: list[LeadPostsResult]
    cost_usd: float | None
    error: str | None = None


class PostOut(ApiModel):
    id: UUID
    lead_id: UUID
    source: str
    url: str
    posted_at: datetime | None
    text: str
    media_type: str
    is_repost: bool
    fetched_at: datetime


# ---------- Company pipeline ----------


class ApolloFindIn(ApiModel):
    # Default to the ICP's search terms and places when empty.
    keywords: list[str] = Field(default=[], max_length=30)
    locations: list[str] = Field(default=[], max_length=30)
    employee_ranges: list[str] = Field(default=[], max_length=10)
    page: int = Field(default=1, ge=1, le=500)
    per_page: int = Field(default=25, ge=1, le=100)


class FindCompaniesIn(ApiModel):
    icp_id: UUID
    sources: list[Literal["lead_intel", "apollo"]] = Field(default=["lead_intel", "apollo"], min_length=1)
    # Only these of the ICP's Lead Intel companies; all of them when not given.
    lead_intel_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=2000)
    apollo: ApolloFindIn = ApolloFindIn()


class SourceFindResult(ApiModel):
    found: int = 0
    added: int = 0
    # Already in the pipeline (same company id or the same website domain).
    duplicates: int = 0
    # Apollo companies that turned out to be in Lead Intel too.
    upgraded: int = 0
    total_available: int | None = None
    error: str | None = None


class FindCompaniesResult(ApiModel):
    lead_intel: SourceFindResult | None
    apollo: SourceFindResult | None


class PipelineCompanyOut(ApiModel):
    id: UUID
    icp_id: UUID | None
    source: str
    lead_intel_business_id: UUID | None
    apollo_org_id: str | None
    name: str
    domain: str | None
    website_url: str | None
    linkedin_url: str | None
    facebook_url: str | None
    twitter_url: str | None
    instagram_url: str | None
    city: str | None
    state: str | None
    status: str
    status_reason: str | None
    overridden: bool
    rule_fit: bool | None
    rule_detail: dict[str, Any] | None
    score: int | None
    score_detail: dict[str, Any] | None
    research: dict[str, Any] | None
    researched_at: datetime | None
    created_at: datetime


class ResearchIn(ApiModel):
    company_ids: list[UUID] = Field(min_length=1, max_length=10)
    # Research again even if done before.
    force: bool = False


class ResearchResult(ApiModel):
    researched: int
    skipped: int
    qualified: int
    not_fit: int
    errors: int
    cost_usd: float
    notes: list[str]


class CompanyStatusIn(ApiModel):
    status: Literal["qualified", "not_fit"]


class PipelineStakeholdersIn(ApiModel):
    company_ids: list[UUID] = Field(min_length=1, max_length=25)
    source: Literal["apollo", "linkedin"] = "apollo"
    titles: list[str] = Field(default=[], max_length=30)
    seniorities: list[str] = Field(default=[], max_length=11)
    max_per_company: int = Field(default=10, ge=1, le=50)


# ---------- Usage ----------


class UsageTotals(ApiModel):
    calls: int
    failed: int
    credits: float
    cost_usd: float
    input_tokens: int
    output_tokens: int


class ProviderUsage(UsageTotals):
    provider: str


class ActionUsage(UsageTotals):
    provider: str
    action: str


class ProviderCallOut(ApiModel):
    idempotency_key: str = Field(serialization_alias="id")
    provider: str
    endpoint: str | None
    action: str | None
    status: str
    credits: float | None
    cost_usd: float
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    called_at: datetime


class UsageOut(ApiModel):
    since: datetime
    providers: list[ProviderUsage]
    actions: list[ActionUsage]
    recent: list[ProviderCallOut]
