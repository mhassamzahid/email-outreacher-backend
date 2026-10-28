"""Tables owned by this service. All of them live in the `outreach` schema and start with `eo_`.

Lead Intel ids (ICPs, businesses) are stored as plain uuids with no foreign key: a foreign key
would make Lead Intel's own deletes and merges fail, which is a change to its tables in effect.
Foreign keys between our own `eo_` tables are fine and used throughout.

Everything a workspace owns carries `workspace_id`; nothing is shared between workspaces.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


OUTREACH_SCHEMA = "outreach"
TABLE_PREFIX = "eo_"

# Constraint and index names derive from the table name, so they carry the eo_ prefix too.
NAMING = {
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "ck": "%(table_name)s_%(constraint_name)s_check",
}

LEAD_STATUSES = ("imported", "enriching", "enriched", "no_content", "generated", "approved", "sent", "skipped", "failed")
SOURCES = ("linkedin", "instagram", "facebook", "website")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _workspace() -> Mapped[UUID]:
    return mapped_column(ForeignKey("eo_workspaces.id", ondelete="CASCADE"), index=True)


def _user_ref() -> Mapped[UUID | None]:
    return mapped_column(ForeignKey("eo_users.id", ondelete="SET NULL"), default=None)


class Base(DeclarativeBase):
    metadata = MetaData(schema=OUTREACH_SCHEMA, naming_convention=NAMING)


# ---------- People and workspaces ----------


class Workspace(Base):
    __tablename__ = "eo_workspaces"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class User(Base):
    """A person who signs in. Admins open every workspace; everyone else only their memberships."""

    __tablename__ = "eo_users"
    __table_args__ = (
        CheckConstraint(_in("role", ("sales", "management", "admin")), name="role"),
        CheckConstraint(_in("status", ("active", "invited", "deactivated")), name="status"),
    )

    id: Mapped[UUID] = _uuid_pk()
    # Supabase auth user, once they've signed in. No foreign key: auth.users isn't ours to constrain.
    auth_user_id: Mapped[UUID | None] = mapped_column(unique=True, default=None)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)  # stored lower-cased
    role: Mapped[str] = mapped_column(Text, server_default="sales")
    status: Mapped[str] = mapped_column(Text, server_default="invited")
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class WorkspaceMember(Base):
    """Which workspaces a Sales or Management user can open."""

    __tablename__ = "eo_workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("eo_workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("eo_users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime] = _created()


class WorkspaceCompany(Base):
    """A Lead Intel business pulled into a workspace. Only the link is stored; the company stays in Lead Intel."""

    __tablename__ = "eo_workspace_companies"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("eo_workspaces.id", ondelete="CASCADE"), primary_key=True)
    # Lead Intel's businesses.id. No foreign key, see module docstring.
    lead_intel_business_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    imported_by: Mapped[UUID | None] = _user_ref()
    imported_at: Mapped[datetime] = _created()


COMPANY_STATUSES = ("found", "qualified", "not_fit", "error")


class Company(Base):
    """A company in a workspace's pipeline for one ICP: found, researched, then qualified or not.

    Found in Lead Intel (already verified, so qualified straight away) or in Apollo (researched:
    website and Instagram read, the ICP's rule checked, scored by Claude). Deduplicated per
    workspace by Lead Intel id, Apollo id, and website domain; a company found in both is the
    Lead Intel one.
    """

    __tablename__ = "eo_companies"
    __table_args__ = (
        CheckConstraint(_in("source", ("lead_intel", "apollo")), name="source"),
        CheckConstraint(_in("status", COMPANY_STATUSES), name="status"),
        CheckConstraint("score is null or score between 0 and 100", name="score"),
        Index("eo_companies_workspace_domain_key", "workspace_id", "domain", unique=True,
              postgresql_where=text("domain is not null")),
        Index("eo_companies_workspace_business_key", "workspace_id", "lead_intel_business_id", unique=True,
              postgresql_where=text("lead_intel_business_id is not null")),
        Index("eo_companies_workspace_apollo_key", "workspace_id", "apollo_org_id", unique=True,
              postgresql_where=text("apollo_org_id is not null")),
        Index("eo_companies_workspace_icp_status_idx", "workspace_id", "icp_id", "status"),
    )  # fmt: skip

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    # The Lead Intel ICP this company was found for (no foreign key: Lead Intel's table).
    icp_id: Mapped[UUID | None] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(Text)
    lead_intel_business_id: Mapped[UUID | None] = mapped_column(default=None)
    apollo_org_id: Mapped[str | None] = mapped_column(Text, default=None)

    name: Mapped[str] = mapped_column(Text)
    # Lower-case, no www: the dedupe key across sources.
    domain: Mapped[str | None] = mapped_column(Text, default=None)
    website_url: Mapped[str | None] = mapped_column(Text, default=None)
    linkedin_url: Mapped[str | None] = mapped_column(Text, default=None)
    facebook_url: Mapped[str | None] = mapped_column(Text, default=None)
    twitter_url: Mapped[str | None] = mapped_column(Text, default=None)
    instagram_url: Mapped[str | None] = mapped_column(Text, default=None)
    city: Mapped[str | None] = mapped_column(Text, default=None)
    state: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[str] = mapped_column(Text, server_default="found")
    # Why it qualified or didn't, in one sentence.
    status_reason: Mapped[str | None] = mapped_column(Text, default=None)
    # Set by hand to keep or drop a company whatever the research said.
    overridden: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rule_fit: Mapped[bool | None] = mapped_column(Boolean, default=None)
    rule_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    score: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    score_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # What was read: website text and pages, socials, Instagram profile, how it was read.
    research: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------- Settings and prompts ----------


class Settings(Base):
    """One row per workspace: defaults, budget and the auto-send rule."""

    __tablename__ = "eo_settings"
    __table_args__ = (
        CheckConstraint("score_threshold between 0 and 100", name="score_threshold"),
        CheckConstraint("posts_per_lead between 1 and 20", name="posts_per_lead"),
        CheckConstraint("max_post_age_days >= 1", name="max_post_age_days"),
        CheckConstraint("monthly_budget_usd >= 0", name="monthly_budget_usd"),
        CheckConstraint("daily_auto_send_cap >= 0", name="daily_auto_send_cap"),
        CheckConstraint("good_examples between 0 and 10", name="good_examples"),
        CheckConstraint("bad_examples between 0 and 10", name="bad_examples"),
        CheckConstraint("improve_examples between 3 and 50", name="improve_examples"),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("eo_workspaces.id", ondelete="CASCADE"), primary_key=True)
    default_model: Mapped[str] = mapped_column(Text, server_default="gpt-6.1-sol")
    posts_per_lead: Mapped[int] = mapped_column(SmallInteger, server_default="5")
    max_post_age_days: Mapped[int] = mapped_column(Integer, server_default="60")
    monthly_budget_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="150")
    # Null uses the workspace's default scoring prompt.
    scoring_prompt_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_prompts.id", ondelete="SET NULL"), default=None)
    score_threshold: Mapped[int] = mapped_column(SmallInteger, server_default="80")
    auto_send: Mapped[bool] = mapped_column(Boolean, server_default="false")
    daily_auto_send_cap: Mapped[int] = mapped_column(Integer, server_default="50")
    # Learning from feedback: how many rated emails Claude sees as examples when it writes an email
    # (the ICP's latest good and bad ones), and how many it reads when suggesting a better prompt.
    good_examples: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    bad_examples: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    improve_examples: Mapped[int] = mapped_column(SmallInteger, server_default="20")
    updated_at: Mapped[datetime] = _updated()


class Prompt(Base):
    __tablename__ = "eo_prompts"
    __table_args__ = (
        # verify: company verification, hook: hook selection, email: email generation, score: email scoring.
        CheckConstraint(_in("kind", ("verify", "hook", "email", "score")), name="kind"),
        # At most one default per kind in a workspace.
        Index(
            "eo_prompts_one_default_idx",
            "workspace_id",
            "kind",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Prompts and versions point at each other, so this key is added after both tables exist.
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eo_prompt_versions.id", ondelete="SET NULL", use_alter=True), default=None
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class PromptVersion(Base):
    """Versions are never edited; saving a prompt adds the next version."""

    __tablename__ = "eo_prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version"),
        CheckConstraint("temperature between 0 and 2", name="temperature"),
        CheckConstraint("max_tokens > 0", name="max_tokens"),
        CheckConstraint("max_posts is null or max_posts between 1 and 50", name="max_posts"),
    )

    id: Mapped[UUID] = _uuid_pk()
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("eo_prompts.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text, server_default="")
    user_prompt: Mapped[str] = mapped_column(Text, server_default="")
    model: Mapped[str] = mapped_column(Text)
    temperature: Mapped[Decimal] = mapped_column(Numeric(3, 2), server_default="0.7")
    max_tokens: Mapped[int] = mapped_column(Integer, server_default="600")
    # How many of the newest posts a hook or email prompt is given ({{items}}). Null: the default, 15.
    max_posts: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[UUID | None] = _user_ref()
    created_at: Mapped[datetime] = _created()


class IcpProfile(Base):
    """What the outreach side needs to know about a Lead Intel ICP to write emails for it."""

    __tablename__ = "eo_icp_profiles"

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("eo_workspaces.id", ondelete="CASCADE"), primary_key=True)
    lead_intel_icp_id: Mapped[UUID] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Stakeholder titles to look for at each company (used by the Apollo search).
    titles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    company_size: Mapped[str] = mapped_column(Text, default="", server_default="")
    pains: Mapped[str] = mapped_column(Text, default="", server_default="")
    offer: Mapped[str] = mapped_column(Text, default="", server_default="")
    proof: Mapped[str] = mapped_column(Text, default="", server_default="")
    cta: Mapped[str] = mapped_column(Text, default="", server_default="")
    tone: Mapped[str] = mapped_column(Text, default="", server_default="")
    example_email: Mapped[str] = mapped_column(Text, default="", server_default="")
    words_to_avoid: Mapped[str] = mapped_column(Text, default="", server_default="")
    hook_guidance: Mapped[str] = mapped_column(Text, default="", server_default="")
    enabled_sources: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    # Null uses the workspace's default prompt of that kind.
    hook_prompt_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_prompts.id", ondelete="SET NULL"), default=None)
    email_prompt_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_prompts.id", ondelete="SET NULL"), default=None)
    verify_prompt_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_prompts.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------- Leads ----------


class Lead(Base):
    """A person we may email: a stakeholder at a company, from Lead Intel, Apollo, a CSV or by hand."""

    __tablename__ = "eo_leads"
    __table_args__ = (
        CheckConstraint(_in("status", LEAD_STATUSES), name="status"),
        CheckConstraint(_in("origin", ("apollo", "apify", "csv", "manual")), name="origin"),
        CheckConstraint("score is null or score between 0 and 100", name="score"),
        # Dedupe within a workspace: same LinkedIn profile or same email is the same person.
        Index("eo_leads_workspace_linkedin_key", "workspace_id", text("lower(linkedin_url)"), unique=True,
              postgresql_where=text("linkedin_url is not null and linkedin_url <> ''")),
        Index("eo_leads_workspace_email_key", "workspace_id", text("lower(email)"), unique=True,
              postgresql_where=text("email is not null and email <> ''")),
        Index("eo_leads_workspace_status_idx", "workspace_id", "status"),
        Index("eo_leads_business_idx", "lead_intel_business_id"),
    )  # fmt: skip

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    # Lead Intel references (no foreign keys, see module docstring).
    lead_intel_icp_id: Mapped[UUID | None] = mapped_column(default=None, index=True)
    lead_intel_business_id: Mapped[UUID | None] = mapped_column(default=None)

    first_name: Mapped[str] = mapped_column(Text, server_default="")
    last_name: Mapped[str] = mapped_column(Text, server_default="")
    email: Mapped[str | None] = mapped_column(Text, default=None)
    # As reported by the provider: verified, likely, guessed, unavailable, invalid...
    email_status: Mapped[str | None] = mapped_column(Text, default=None)
    title: Mapped[str] = mapped_column(Text, server_default="")
    company: Mapped[str] = mapped_column(Text, server_default="")
    website: Mapped[str] = mapped_column(Text, server_default="")
    domain: Mapped[str | None] = mapped_column(Text, default=None)
    linkedin_url: Mapped[str | None] = mapped_column(Text, default=None)
    instagram_url: Mapped[str | None] = mapped_column(Text, default=None)
    facebook_url: Mapped[str | None] = mapped_column(Text, default=None)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    origin: Mapped[str] = mapped_column(Text)
    # The provider's id for this person (Apollo person id, Apify profile id), for re-lookups.
    external_id: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="imported")

    score: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    score_reasons: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
    score_prompt_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eo_prompt_versions.id", ondelete="SET NULL"), default=None
    )
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_by: Mapped[UUID | None] = _user_ref()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Stakeholder(Base):
    """A person found at a company by an Apollo or Apify search, before anyone picks them.

    Searching is free; revealing an email costs credits. Candidates wait here until chosen, and
    choosing one creates the lead (`lead_id`).
    """

    __tablename__ = "eo_stakeholders"
    __table_args__ = (
        CheckConstraint(_in("source", ("apollo", "apify_linkedin")), name="source"),
        UniqueConstraint("workspace_id", "source", "external_id"),
        Index("eo_stakeholders_business_idx", "workspace_id", "lead_intel_business_id"),
        Index("eo_stakeholders_icp_idx", "workspace_id", "lead_intel_icp_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    lead_intel_business_id: Mapped[UUID | None] = mapped_column(default=None)
    # The pipeline company they work at, when found through one.
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_companies.id", ondelete="SET NULL"), default=None, index=True)
    # The Lead Intel ICP whose search found them, when found by ICP rather than by company.
    lead_intel_icp_id: Mapped[UUID | None] = mapped_column(default=None)
    # Their employer as Apollo names it; the only company we know for people found by ICP.
    organization_name: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(Text, server_default="")
    last_name: Mapped[str] = mapped_column(Text, server_default="")
    title: Mapped[str] = mapped_column(Text, server_default="")
    seniority: Mapped[str | None] = mapped_column(Text, default=None)
    linkedin_url: Mapped[str | None] = mapped_column(Text, default=None)
    has_email: Mapped[bool | None] = mapped_column(Boolean, default=None)
    email: Mapped[str | None] = mapped_column(Text, default=None)
    email_status: Mapped[str | None] = mapped_column(Text, default=None)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_leads.id", ondelete="SET NULL"), default=None)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    found_at: Mapped[datetime] = _created()


# ---------- Jobs and spend ----------


class Job(Base):
    """A long-running piece of work (Apify run, bulk reveal, enrichment, drafting) and its progress."""

    __tablename__ = "eo_jobs"
    __table_args__ = (
        CheckConstraint(_in("status", ("queued", "running", "done", "failed", "cancelled")), name="status"),
        Index("eo_jobs_workspace_status_idx", "workspace_id", "status"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    kind: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)  # what the person sees: "Finding owners at 12 companies"
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    total: Mapped[int | None] = mapped_column(Integer, default=None)  # null until the job knows its size
    done: Mapped[int] = mapped_column(Integer, server_default="0")
    step: Mapped[str | None] = mapped_column(Text, default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    external_run_id: Mapped[str | None] = mapped_column(Text, default=None)  # e.g. the Apify run id
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="0")
    created_by: Mapped[UUID | None] = _user_ref()
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Last time the worker reported anything; a running job that stops moving is stalled.
    updated_at: Mapped[datetime] = _updated()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ProviderCall(Base):
    """Every paid call: the spend ledger, and the check that stops a retry paying twice."""

    __tablename__ = "eo_provider_calls"
    __table_args__ = (
        CheckConstraint(_in("status", ("succeeded", "failed")), name="status"),
        Index("eo_provider_calls_workspace_called_idx", "workspace_id", "called_at"),
    )

    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[UUID] = _workspace()
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_jobs.id", ondelete="SET NULL"), default=None)
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_leads.id", ondelete="SET NULL"), default=None)
    provider: Mapped[str] = mapped_column(Text)  # apollo | apify | claude | cheapsequencer
    endpoint: Mapped[str | None] = mapped_column(Text, default=None)
    action: Mapped[str | None] = mapped_column(Text, default=None)  # what the user did: "Find companies", "Research"...
    model: Mapped[str | None] = mapped_column(Text, default=None)  # Claude model, when provider is claude
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="0")
    credits: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)  # Apollo credits, when relevant
    called_at: Mapped[datetime] = _created()


# ---------- Content and insights ----------


class Post(Base):
    """A recent post: a lead's (LinkedIn or Instagram), or a pipeline company's own Instagram post."""

    __tablename__ = "eo_posts"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source"),
        CheckConstraint(_in("media_type", ("text", "image", "video", "carousel", "article", "page")), name="media_type"),
        CheckConstraint("lead_id is not null or company_id is not null", name="owner"),
        UniqueConstraint("lead_id", "url"),
        UniqueConstraint("company_id", "url"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_leads.id", ondelete="CASCADE"), default=None)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_companies.id", ondelete="CASCADE"), default=None)
    source: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text, default=None)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    text: Mapped[str] = mapped_column(Text, server_default="")
    media_type: Mapped[str] = mapped_column(Text, server_default="text")
    is_repost: Mapped[bool] = mapped_column(Boolean, server_default="false")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_jobs.id", ondelete="SET NULL"), default=None)
    fetched_at: Mapped[datetime] = _created()


class Insight(Base):
    """A ranked point worth mentioning to a lead, drawn from their posts or their company's site."""

    __tablename__ = "eo_insights"
    __table_args__ = (
        CheckConstraint("relevance between 0 and 5", name="relevance"),
        Index("eo_insights_lead_rank_idx", "lead_id", "rank"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("eo_leads.id", ondelete="CASCADE"))
    post_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_posts.id", ondelete="CASCADE"), default=None)
    rank: Mapped[int] = mapped_column(SmallInteger)  # 1 is the strongest
    point: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, server_default="")
    relevance: Mapped[int] = mapped_column(SmallInteger)
    prompt_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eo_prompt_versions.id", ondelete="SET NULL"), default=None
    )
    model: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = _created()


# ---------- Emails and sending ----------


class Email(Base):
    """A drafted email for a lead, from draft through approval to sent."""

    __tablename__ = "eo_emails"
    __table_args__ = (
        CheckConstraint(_in("status", ("draft", "edited", "approved", "sent", "skipped")), name="status"),
        CheckConstraint("sent_by is null or " + _in("sent_by", ("auto", "manual")), name="sent_by"),
        CheckConstraint("relevance between 0 and 5", name="relevance"),
        CheckConstraint("confidence between 0 and 100", name="confidence"),
        Index("eo_emails_workspace_status_idx", "workspace_id", "status"),
        Index("eo_emails_lead_idx", "lead_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("eo_leads.id", ondelete="CASCADE"))
    hook_prompt_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eo_prompt_versions.id", ondelete="SET NULL"), default=None
    )
    email_prompt_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eo_prompt_versions.id", ondelete="SET NULL"), default=None
    )
    chosen_post_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_posts.id", ondelete="SET NULL"), default=None)
    hook: Mapped[str] = mapped_column(Text, server_default="")
    relevance: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    edited_body: Mapped[str | None] = mapped_column(Text, default=None)
    confidence: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="0")
    approved_by: Mapped[UUID | None] = _user_ref()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sent_by: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Where CheapSequencer holds it once queued.
    sequencer_list_id: Mapped[str | None] = mapped_column(Text, default=None)
    sequencer_lead_id: Mapped[str | None] = mapped_column(Text, default=None)
    # The rated emails shown to Claude as examples when this one was written (see EmailFeedback).
    examples_used: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SendEvent(Base):
    """What happened after sending, from CheapSequencer's webhooks."""

    __tablename__ = "eo_send_events"
    __table_args__ = (
        CheckConstraint(
            _in("type", ("queued", "sent", "opened", "replied", "bounced", "unsubscribed", "failed")), name="type"
        ),
        Index("eo_send_events_email_idx", "email_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    email_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_emails.id", ondelete="SET NULL"), default=None)
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("eo_leads.id", ondelete="SET NULL"), default=None)
    type: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, server_default="cheapsequencer")
    # The provider's event id: a webhook delivered twice is stored once.
    provider_event_id: Mapped[str | None] = mapped_column(Text, unique=True, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = _created()


class CompanyPage(Base):
    """A page of a pipeline company's website as it was read: its full text, up to 50,000 characters."""

    __tablename__ = "eo_company_pages"
    __table_args__ = (UniqueConstraint("company_id", "url"),)

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    company_id: Mapped[UUID] = mapped_column(ForeignKey("eo_companies.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)  # homepage | contact | social | about
    via: Mapped[str] = mapped_column(Text)  # own_crawl | jina
    status: Mapped[int | None] = mapped_column(Integer, default=None)
    text: Mapped[str] = mapped_column(Text, server_default="")
    fetched_at: Mapped[datetime] = _created()


FEEDBACK_TAGS = (
    # Good
    "strong_hook", "right_tone", "clear_cta", "send_as_is",
    # Bad
    "too_long", "too_generic", "wrong_hook", "tone_off", "made_up_facts", "weak_cta", "wrong_person",
)  # fmt: skip


class EmailFeedback(Base):
    """A person's verdict on a generated email: good or bad, why, and the text as it was rated.

    Rated emails for an ICP are shown to Claude as examples when it writes the next ones, and feed
    the "improve prompt" suggestion. Nothing is trained; the feedback is used in the prompt.
    """

    __tablename__ = "eo_email_feedback"
    __table_args__ = (
        CheckConstraint(_in("rating", ("good", "bad")), name="rating"),
        Index("eo_email_feedback_icp_idx", "workspace_id", "lead_intel_icp_id", "rating", "updated_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workspace_id: Mapped[UUID] = _workspace()
    email_id: Mapped[UUID] = mapped_column(ForeignKey("eo_emails.id", ondelete="CASCADE"), unique=True)
    # The lead's ICP when rated, so examples can be picked per ICP (Lead Intel reference, no foreign key).
    lead_intel_icp_id: Mapped[UUID | None] = mapped_column(default=None)
    rating: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    note: Mapped[str] = mapped_column(Text, server_default="")
    # The email as it was when rated (edits included), so later edits don't change what was judged.
    subject: Mapped[str] = mapped_column(Text, server_default="")
    body: Mapped[str] = mapped_column(Text, server_default="")
    hook: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
