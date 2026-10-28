"""Reads and writes for our own `outreach.eo_` tables. Lead Intel is read in lead_intel.py, never here."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Company, CompanyPage, Email, EmailFeedback, IcpProfile, Settings, Insight, Lead, Post, Prompt, PromptVersion, ProviderCall, Stakeholder,
    Workspace, WorkspaceCompany,
)  # fmt: skip
from services.apify import Employee, FetchedPost
from services.apollo import ApolloOrganization, FoundPerson, RevealedPerson
from services.leads import Existing, LeadInput
from services.prompts import DEFAULTS


class IcpProfileRepo:
    """Reads and writes `outreach.eo_icp_profiles` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def get_many(self, workspace_id: UUID, icp_ids: list[UUID]) -> dict[UUID, IcpProfile]:
        if not icp_ids:
            return {}
        result = await self.session.scalars(
            select(IcpProfile).where(IcpProfile.workspace_id == workspace_id, IcpProfile.lead_intel_icp_id.in_(icp_ids))
        )
        return {p.lead_intel_icp_id: p for p in result}

    async def upsert(self, workspace_id: UUID, icp_id: UUID, values: dict) -> IcpProfile:
        statement = (
            insert(IcpProfile)
            .values(workspace_id=workspace_id, lead_intel_icp_id=icp_id, **values)
            .on_conflict_do_update(
                index_elements=[IcpProfile.workspace_id, IcpProfile.lead_intel_icp_id],
                set_={**values, "updated_at": func.now()},
            )
            .returning(IcpProfile)
        )
        profile = (await self.session.scalars(statement)).one()
        await self.session.commit()
        return profile


class PromptRepo:
    """Prompt templates and their versions. `outreach.eo_prompts` and `eo_prompt_versions` only.

    Every workspace has one default prompt of each kind; missing ones are created from the
    built-in defaults the first time the workspace's prompts are read.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def ensure_defaults(self, workspace_id: UUID) -> None:
        have = set(await self.session.scalars(select(Prompt.kind).where(Prompt.workspace_id == workspace_id).distinct()))
        missing = [k for k in DEFAULTS if k not in have]
        if not missing:
            return
        for kind in missing:
            d = DEFAULTS[kind]
            prompt = Prompt(workspace_id=workspace_id, name=d.name, kind=kind, is_default=True)
            self.session.add(prompt)
            await self.session.flush()
            version = PromptVersion(
                prompt_id=prompt.id, version=1, system_prompt=d.system_prompt, user_prompt=d.user_prompt, model=d.model,
                temperature=d.temperature, max_tokens=d.max_tokens, note="Built-in default",
            )  # fmt: skip
            self.session.add(version)
            await self.session.flush()
            prompt.current_version_id = version.id
        await self.session.commit()

    async def list(self, workspace_id: UUID) -> tuple[list[Prompt], list[PromptVersion]]:
        prompts = list(
            await self.session.scalars(
                select(Prompt).where(Prompt.workspace_id == workspace_id).order_by(Prompt.kind, Prompt.created_at)
            )
        )
        if not prompts:
            return [], []
        versions = await self.session.scalars(
            select(PromptVersion)
            .where(PromptVersion.prompt_id.in_([p.id for p in prompts]))
            .order_by(PromptVersion.prompt_id, PromptVersion.version)
        )
        return prompts, list(versions)

    async def get(self, workspace_id: UUID, prompt_id: UUID) -> Prompt | None:
        prompt = await self.session.get(Prompt, prompt_id)
        return prompt if prompt and prompt.workspace_id == workspace_id else None

    async def current_version(self, prompt: Prompt) -> PromptVersion | None:
        return await self.session.get(PromptVersion, prompt.current_version_id) if prompt.current_version_id else None

    async def default(self, workspace_id: UUID, kind: str) -> Prompt | None:
        return await self.session.scalar(
            select(Prompt).where(Prompt.workspace_id == workspace_id, Prompt.kind == kind, Prompt.is_default)
        )

    async def for_icp(self, workspace_id: UUID, kind: str, chosen_id: UUID | None) -> PromptVersion | None:
        """The current version of the ICP's chosen prompt of this kind, else the workspace default's."""
        prompt = await self.get(workspace_id, chosen_id) if chosen_id else None
        if prompt is None or prompt.kind != kind:
            await self.ensure_defaults(workspace_id)
            prompt = await self.default(workspace_id, kind)
        return await self.current_version(prompt) if prompt else None

    async def create(
        self, workspace_id: UUID, name: str, kind: str, draft: dict[str, Any], note: str
    ) -> tuple[Prompt, PromptVersion]:
        await self.ensure_defaults(workspace_id)
        prompt = Prompt(workspace_id=workspace_id, name=name, kind=kind, is_default=False)
        self.session.add(prompt)
        await self.session.flush()
        version = await self._add_version(prompt, draft, note)
        await self.session.commit()
        return prompt, version

    async def add_version(self, prompt: Prompt, draft: dict[str, Any], note: str) -> PromptVersion:
        version = await self._add_version(prompt, draft, note)
        await self.session.commit()
        return version

    async def _add_version(self, prompt: Prompt, draft: dict[str, Any], note: str) -> PromptVersion:
        latest = await self.session.scalar(
            select(func.max(PromptVersion.version)).where(PromptVersion.prompt_id == prompt.id)
        )
        version = PromptVersion(prompt_id=prompt.id, version=(latest or 0) + 1, note=note, **draft)
        self.session.add(version)
        await self.session.flush()
        prompt.current_version_id = version.id
        prompt.updated_at = datetime.now(UTC)
        return version

    async def rename(self, prompt: Prompt, name: str) -> None:
        prompt.name = name
        prompt.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(prompt)

    async def make_default(self, prompt: Prompt) -> None:
        # One default per kind (a unique index): clear the old one before setting the new one.
        old = await self.default(prompt.workspace_id, prompt.kind)
        if old and old.id != prompt.id:
            old.is_default = False
            await self.session.flush()
        prompt.is_default = True
        await self.session.commit()
        # The row's update timestamp is set by the database; read it back.
        await self.session.refresh(prompt)

    async def in_use(self, prompt: Prompt) -> int:
        """How many ICP profiles chose this prompt."""
        column = {
            "verify": IcpProfile.verify_prompt_id, "hook": IcpProfile.hook_prompt_id, "email": IcpProfile.email_prompt_id,
        }.get(prompt.kind)  # fmt: skip
        if column is None:
            return 0
        return await self.session.scalar(select(func.count()).select_from(IcpProfile).where(column == prompt.id)) or 0

    async def delete(self, prompt: Prompt) -> None:
        await self.session.delete(prompt)
        await self.session.commit()


_full_name = func.lower(func.trim(func.concat(Lead.first_name, " ", Lead.last_name)))


class LeadRepo:
    """Reads and writes `outreach.eo_leads` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def find_existing(self, workspace_id: UUID, leads: list[LeadInput]) -> Existing:
        """Which of these leads' emails, LinkedIn URLs and domain+name pairs the workspace already has."""
        emails = {lead.email for lead in leads if lead.email}
        linkedins = {lead.linkedin_url.lower() for lead in leads if lead.linkedin_url}
        name_keys = {lead.name_key for lead in leads if lead.name_key}
        conditions = []
        if emails:
            conditions.append(func.lower(Lead.email).in_(emails))
        if linkedins:
            conditions.append(func.lower(Lead.linkedin_url).in_(linkedins))
        if name_keys:
            conditions.append(tuple_(Lead.domain, _full_name).in_(list(name_keys)))
        found = Existing()
        if not conditions:
            return found
        rows = await self.session.execute(
            select(func.lower(Lead.email), func.lower(Lead.linkedin_url), Lead.domain, _full_name).where(
                Lead.workspace_id == workspace_id, or_(*conditions)
            )
        )
        for email, linkedin, domain, name in rows:
            if email:
                found.emails.add(email)
            if linkedin:
                found.linkedins.add(linkedin)
            if domain and name:
                found.name_keys.add((domain, name))
        return found

    async def insert_many(
        self, workspace_id: UUID, icp_id: UUID | None, origin: str, leads: list[LeadInput]
    ) -> list[UUID | None]:
        """Insert in order; the id of each new lead, or None where the database found it a duplicate.

        The unique indexes on email and LinkedIn URL are the final word: a lead added by someone else
        between the duplicate check and this insert is skipped, not doubled.
        """
        ids: list[UUID | None] = []
        for lead in leads:
            statement = (
                insert(Lead)
                .values(
                    workspace_id=workspace_id,
                    lead_intel_icp_id=icp_id,
                    first_name=lead.first_name,
                    last_name=lead.last_name,
                    email=lead.email or None,
                    title=lead.title,
                    company=lead.company,
                    website=lead.website,
                    domain=lead.domain or None,
                    linkedin_url=lead.linkedin_url or None,
                    instagram_url=lead.instagram_url or None,
                    facebook_url=lead.facebook_url or None,
                    extra=lead.extra,
                    origin=origin,
                )
                .on_conflict_do_nothing()
                .returning(Lead.id)
            )
            ids.append((await self.session.execute(statement)).scalar_one_or_none())
        await self.session.commit()
        return ids

    async def get(self, workspace_id: UUID, lead_id: UUID) -> Lead | None:
        return await self.session.scalar(select(Lead).where(Lead.workspace_id == workspace_id, Lead.id == lead_id))

    async def list(
        self, workspace_id: UUID, icp_id: UUID | None, status: str | None, q: str | None, limit: int, offset: int
    ) -> tuple[list[Lead], int]:
        where: list[Any] = [Lead.workspace_id == workspace_id]
        if icp_id:
            where.append(Lead.lead_intel_icp_id == icp_id)
        if status:
            where.append(Lead.status == status)
        if q:
            like = f"%{q}%"
            where.append(or_(Lead.email.ilike(like), Lead.company.ilike(like), _full_name.ilike(like)))
        total = (await self.session.execute(select(func.count()).select_from(Lead).where(*where))).scalar_one()
        rows = await self.session.scalars(
            select(Lead).where(*where).order_by(Lead.created_at.desc(), Lead.id).limit(limit).offset(offset)
        )
        return list(rows), total


class WorkspaceCompanyRepo:
    """Which Lead Intel companies are in a workspace. Reads and writes `outreach.eo_workspace_companies` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def imported(self, workspace_id: UUID) -> dict[UUID, datetime]:
        rows = await self.session.execute(
            select(WorkspaceCompany.lead_intel_business_id, WorkspaceCompany.imported_at).where(
                WorkspaceCompany.workspace_id == workspace_id
            )
        )
        return dict(rows.tuples().all())

    async def add(self, workspace_id: UUID, company_ids: list[UUID]) -> int:
        """Adds the ones not already there; returns how many were new."""
        if not company_ids:
            return 0
        statement = (
            insert(WorkspaceCompany)
            .values([{"workspace_id": workspace_id, "lead_intel_business_id": cid} for cid in dict.fromkeys(company_ids)])
            .on_conflict_do_nothing()
            .returning(WorkspaceCompany.lead_intel_business_id)
        )
        added = len((await self.session.execute(statement)).all())
        await self.session.commit()
        return added

    async def remove(self, workspace_id: UUID, company_ids: list[UUID]) -> int:
        statement = (
            delete(WorkspaceCompany)
            .where(WorkspaceCompany.workspace_id == workspace_id, WorkspaceCompany.lead_intel_business_id.in_(company_ids))
            .returning(WorkspaceCompany.lead_intel_business_id)
        )
        removed = len((await self.session.execute(statement)).all())
        await self.session.commit()
        return removed


class WorkspaceRepo:
    """Reads and writes `outreach.eo_workspaces` only.

    Deleting a workspace also deletes everything it owns in our `eo_` tables (their foreign keys
    cascade). Lead Intel is never touched: workspaces only ever point at it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self) -> list[Workspace]:
        return list(await self.session.scalars(select(Workspace).order_by(Workspace.created_at, Workspace.name)))

    async def get(self, workspace_id: UUID) -> Workspace | None:
        return await self.session.get(Workspace, workspace_id)

    async def name_taken(self, name: str, except_id: UUID | None = None) -> bool:
        query = select(Workspace.id).where(func.lower(Workspace.name) == name.lower())
        if except_id:
            query = query.where(Workspace.id != except_id)
        return (await self.session.scalar(query)) is not None

    async def create(self, name: str, description: str) -> Workspace:
        workspace = Workspace(name=name, description=description)
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def update(self, workspace: Workspace, values: dict[str, Any]) -> Workspace:
        for key, value in values.items():
            setattr(workspace, key, value)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def delete(self, workspace: Workspace) -> None:
        await self.session.delete(workspace)
        await self.session.commit()


class StakeholderRepo:
    """People found at a company, before and after their email is revealed. `outreach.eo_stakeholders`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self, workspace_id: UUID, company_id: UUID | None = None, icp_id: UUID | None = None
    ) -> list[Stakeholder]:
        query = select(Stakeholder).where(Stakeholder.workspace_id == workspace_id)
        if company_id:
            query = query.where(Stakeholder.lead_intel_business_id == company_id)
        if icp_id:
            query = query.where(Stakeholder.lead_intel_icp_id == icp_id)
        return list(await self.session.scalars(query.order_by(Stakeholder.found_at.desc(), Stakeholder.id)))

    async def get_many(self, workspace_id: UUID, ids: list[UUID]) -> list[Stakeholder]:
        query = select(Stakeholder).where(Stakeholder.workspace_id == workspace_id, Stakeholder.id.in_(ids))
        return list(await self.session.scalars(query))

    async def save_found(
        self,
        workspace_id: UUID,
        company_id: UUID | None,
        people: list[FoundPerson],
        icp_id: UUID | None = None,
        pipeline_company_id: UUID | None = None,
    ) -> int:
        """Add new search hits and refresh known ones; returns how many were new.

        A person who was already revealed keeps their full name and email: search results only
        carry a masked last name and must not overwrite what was paid for.
        """
        if not people:
            return 0
        known = set(
            await self.session.scalars(
                select(Stakeholder.external_id).where(
                    Stakeholder.workspace_id == workspace_id,
                    Stakeholder.source == "apollo",
                    Stakeholder.external_id.in_([p.apollo_id for p in people]),
                )
            )
        )
        for p in people:
            statement = insert(Stakeholder).values(
                workspace_id=workspace_id,
                lead_intel_business_id=company_id,
                company_id=pipeline_company_id,
                lead_intel_icp_id=icp_id,
                organization_name=p.organization_name,
                source="apollo",
                external_id=p.apollo_id,
                first_name=p.first_name,
                last_name=p.last_name_masked,
                title=p.title,
                has_email=p.has_email,
                raw=p.raw,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[Stakeholder.workspace_id, Stakeholder.source, Stakeholder.external_id],
                set_={
                    "title": statement.excluded.title,
                    "has_email": statement.excluded.has_email,
                    # A search by ICP knows no Lead Intel company; it must not wipe one a company search set.
                    "lead_intel_business_id": func.coalesce(
                        statement.excluded.lead_intel_business_id, Stakeholder.lead_intel_business_id
                    ),
                    "lead_intel_icp_id": func.coalesce(Stakeholder.lead_intel_icp_id, statement.excluded.lead_intel_icp_id),
                    "organization_name": func.coalesce(statement.excluded.organization_name, Stakeholder.organization_name),
                    "company_id": func.coalesce(statement.excluded.company_id, Stakeholder.company_id),
                    "raw": case((Stakeholder.revealed_at.is_(None), statement.excluded.raw), else_=Stakeholder.raw),
                    "last_name": case(
                        (Stakeholder.revealed_at.is_(None), statement.excluded.last_name), else_=Stakeholder.last_name
                    ),
                },
            )
            await self.session.execute(statement)
        await self.session.commit()
        return sum(p.apollo_id not in known for p in people)

    async def save_employees(
        self, workspace_id: UUID, company_id: UUID | None, people: list[Employee], pipeline_company_id: UUID | None = None
    ) -> int:
        """Add people found on LinkedIn (via Apify) and refresh known ones; returns how many were new.

        Unlike an Apollo search these come with full names and a LinkedIn URL, but no email.
        """
        if not people:
            return 0
        known = set(
            await self.session.scalars(
                select(Stakeholder.external_id).where(
                    Stakeholder.workspace_id == workspace_id,
                    Stakeholder.source == "apify_linkedin",
                    Stakeholder.external_id.in_([p.external_id for p in people]),
                )
            )
        )
        for p in people:
            statement = insert(Stakeholder).values(
                workspace_id=workspace_id,
                lead_intel_business_id=company_id,
                company_id=pipeline_company_id,
                source="apify_linkedin",
                external_id=p.external_id,
                first_name=p.first_name,
                last_name=p.last_name,
                title=p.title,
                linkedin_url=p.linkedin_url,
                organization_name=p.company_name,
                raw=p.raw,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[Stakeholder.workspace_id, Stakeholder.source, Stakeholder.external_id],
                set_={
                    "title": statement.excluded.title,
                    "linkedin_url": func.coalesce(statement.excluded.linkedin_url, Stakeholder.linkedin_url),
                    "organization_name": func.coalesce(statement.excluded.organization_name, Stakeholder.organization_name),
                    "lead_intel_business_id": func.coalesce(
                        statement.excluded.lead_intel_business_id, Stakeholder.lead_intel_business_id
                    ),
                    "company_id": func.coalesce(statement.excluded.company_id, Stakeholder.company_id),
                },
            )
            await self.session.execute(statement)
        await self.session.commit()
        return sum(p.external_id not in known for p in people)

    async def save_person(
        self, workspace_id: UUID, icp_id: UUID | None, person: Any, company: Company | None, origin: str
    ) -> tuple[Stakeholder, bool]:
        """Add a person typed in or imported (services.people_csv.PersonRow), or update the one already
        stored under the same id. Returns (the person, whether they're new). A known email is never
        replaced by an empty one."""
        source, external_id = person.external_id
        values = {
            "first_name": person.first_name, "last_name": person.last_name, "title": person.title,
            "seniority": person.seniority or None, "linkedin_url": person.linkedin_url or None,
            "email": person.email or None, "email_status": person.email_status or None,
            "has_email": True if person.email else None,
            "organization_name": (company.name if company else None) or person.company or None,
            "company_id": company.id if company else None,
            "lead_intel_business_id": company.lead_intel_business_id if company else None,
            "lead_intel_icp_id": icp_id or (company.icp_id if company else None),
            "raw": {
                "added": origin, "company": person.company, "website": person.website, "phone": person.phone,
                "city": person.city, "state": person.state, "country": person.country,
                "company_linkedin_url": person.company_linkedin_url, "facebook_url": person.facebook_url,
                "twitter_url": person.twitter_url, "apollo_account_id": person.apollo_account_id, "extra": person.extra,
            },
        }  # fmt: skip
        known = await self.session.scalar(
            select(Stakeholder).where(
                Stakeholder.workspace_id == workspace_id, Stakeholder.source == source, Stakeholder.external_id == external_id
            )
        )
        if known is None:
            row = Stakeholder(workspace_id=workspace_id, source=source, external_id=external_id,
                              revealed_at=func.now() if person.email else None, **values)  # fmt: skip
            self.session.add(row)
            await self.session.flush()
            return row, True
        # Blank cells never wipe what's stored; what the CSV adds is merged into the stored details.
        for key, value in values.items():
            if key == "raw":
                known.raw = {**(known.raw or {}), **value}
            elif value not in (None, ""):
                setattr(known, key, value)
        if person.email and known.revealed_at is None:
            known.revealed_at = func.now()
        return known, False

    async def commit(self) -> None:
        await self.session.commit()

    async def link_lead(self, stakeholder: Stakeholder, lead_id: UUID) -> None:
        stakeholder.lead_id = lead_id
        await self.session.commit()

    async def save_revealed(self, stakeholder: Stakeholder, person: RevealedPerson | None, lead_id: UUID | None) -> None:
        stakeholder.revealed_at = func.now()
        if person is None:
            # Apollo found no match and charged nothing; remember that so nobody pays to ask again.
            stakeholder.email_status = "no_match"
        else:
            stakeholder.first_name = person.first_name or stakeholder.first_name
            stakeholder.last_name = person.last_name
            stakeholder.title = person.title or stakeholder.title
            stakeholder.seniority = person.seniority
            stakeholder.email = person.email
            stakeholder.email_status = person.email_status
            stakeholder.linkedin_url = person.linkedin_url
            stakeholder.raw = person.raw
        stakeholder.lead_id = lead_id
        await self.session.commit()
        await self.session.refresh(stakeholder)


class ProviderCallRepo:
    """The ledger of calls to paid providers. `outreach.eo_provider_calls`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        workspace_id: UUID,
        provider: str,
        endpoint: str,
        status: str,
        *,
        key: str | None = None,
        lead_id: UUID | None = None,
        credits: float | None = None,
        cost_usd: float | None = None,
        action: str | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        await self.session.execute(
            insert(ProviderCall)
            .values(
                idempotency_key=key or f"{provider}:{endpoint}:{uuid4()}",
                workspace_id=workspace_id,
                lead_id=lead_id,
                provider=provider,
                endpoint=endpoint,
                status=status,
                credits=credits,
                cost_usd=cost_usd or 0,
                action=action,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            .on_conflict_do_nothing()
        )
        await self.session.commit()

    async def usage(self, workspace_id: UUID, since: datetime, recent: int = 100) -> dict[str, Any]:
        """Spend since a date: totals per provider, per provider and action, and the latest calls."""
        where = (ProviderCall.workspace_id == workspace_id, ProviderCall.called_at >= since)
        sums = (
            func.count().label("calls"),
            func.count().filter(ProviderCall.status == "failed").label("failed"),
            func.coalesce(func.sum(ProviderCall.credits), 0).label("credits"),
            func.coalesce(func.sum(ProviderCall.cost_usd), 0).label("cost_usd"),
            func.coalesce(func.sum(ProviderCall.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ProviderCall.output_tokens), 0).label("output_tokens"),
        )
        action = func.coalesce(ProviderCall.action, ProviderCall.endpoint, "Other").label("action")
        by_provider = await self.session.execute(
            select(ProviderCall.provider, *sums).where(*where).group_by(ProviderCall.provider)
        )
        by_action = await self.session.execute(
            select(ProviderCall.provider, action, *sums).where(*where).group_by(ProviderCall.provider, action)
        )
        latest = await self.session.scalars(
            select(ProviderCall).where(*where).order_by(ProviderCall.called_at.desc()).limit(recent)
        )
        return {
            "providers": [dict(r._mapping) for r in by_provider],
            "actions": [dict(r._mapping) for r in by_action],
            "recent": list(latest),
        }


async def lead_from_stakeholder(
    session: AsyncSession, workspace_id: UUID, icp_id: UUID | None, company_id: UUID | None, lead: LeadInput, apollo_id: str,
    email_status: str | None, origin: str = "apollo",
) -> tuple[UUID | None, bool]:  # fmt: skip
    """The lead for a revealed person: created, or the existing one with the same email or LinkedIn.

    Returns (lead id, whether it was created). The unique indexes decide what counts as the same person.
    """
    statement = (
        insert(Lead)
        .values(
            workspace_id=workspace_id,
            lead_intel_icp_id=icp_id,
            lead_intel_business_id=company_id,
            first_name=lead.first_name,
            last_name=lead.last_name,
            email=lead.email or None,
            email_status=email_status,
            title=lead.title,
            company=lead.company,
            website=lead.website,
            domain=lead.domain or None,
            linkedin_url=lead.linkedin_url or None,
            origin=origin,
            external_id=apollo_id,
        )
        .on_conflict_do_nothing()
        .returning(Lead.id)
    )
    created = (await session.execute(statement)).scalar_one_or_none()
    await session.commit()
    if created:
        return created, True
    matches = []
    if lead.email:
        matches.append(func.lower(Lead.email) == lead.email)
    if lead.linkedin_url:
        matches.append(func.lower(Lead.linkedin_url) == lead.linkedin_url.lower())
    if not matches:
        return None, False
    existing = await session.scalar(select(Lead.id).where(Lead.workspace_id == workspace_id, or_(*matches)).limit(1))
    return existing, False


class PostRepo:
    """Leads' recent posts. Writes `outreach.eo_posts`, and the lead's status once its posts are in."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def leads(self, workspace_id: UUID, lead_ids: list[UUID]) -> list[Lead]:
        query = select(Lead).where(Lead.workspace_id == workspace_id, Lead.id.in_(lead_ids))
        return list(await self.session.scalars(query))

    async def save(self, workspace_id: UUID, lead_id: UUID, posts: list[FetchedPost]) -> int:
        """Store posts a lead doesn't have yet (same URL = same post); returns how many were new."""
        new = 0
        for p in posts:
            statement = (
                insert(Post)
                .values(
                    workspace_id=workspace_id,
                    lead_id=lead_id,
                    source=p.source,
                    url=p.url,
                    external_id=p.external_id,
                    posted_at=p.posted_at,
                    text=p.text,
                    media_type=p.media_type,
                    is_repost=p.is_repost,
                    raw=p.raw,
                )
                .on_conflict_do_nothing()
                .returning(Post.id)
            )
            new += (await self.session.execute(statement)).scalar_one_or_none() is not None
        await self.session.commit()
        return new

    async def set_status(self, lead: Lead, status: str) -> None:
        lead.status = status
        await self.session.commit()

    async def save_company(self, workspace_id: UUID, company_id: UUID, posts: list[FetchedPost]) -> int:
        """Store a pipeline company's own posts it doesn't have yet; returns how many were new."""
        new = 0
        for p in posts:
            statement = (
                insert(Post)
                .values(
                    workspace_id=workspace_id, company_id=company_id, source=p.source, url=p.url, external_id=p.external_id,
                    posted_at=p.posted_at, text=p.text, media_type=p.media_type, is_repost=p.is_repost, raw=p.raw,
                )
                .on_conflict_do_nothing()
                .returning(Post.id)
            )  # fmt: skip
            new += (await self.session.execute(statement)).scalar_one_or_none() is not None
        await self.session.commit()
        return new

    async def for_company(self, workspace_id: UUID, company_id: UUID) -> list[Post]:
        query = select(Post).where(Post.workspace_id == workspace_id, Post.company_id == company_id)
        return list(await self.session.scalars(query.order_by(Post.posted_at.desc().nulls_last(), Post.id)))

    async def list(self, workspace_id: UUID, lead_id: UUID | None) -> list[Post]:
        # Leads' posts only; a company's own posts are read with for_company.
        query = select(Post).where(Post.workspace_id == workspace_id, Post.lead_id.is_not(None))
        if lead_id:
            query = query.where(Post.lead_id == lead_id)
        return list(await self.session.scalars(query.order_by(Post.posted_at.desc().nulls_last(), Post.id)))


class CompanyRepo:
    """The workspace's company pipeline. Reads and writes `outreach.eo_companies` and `eo_company_pages` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def get_many(self, workspace_id: UUID, ids: list[UUID]) -> list[Company]:
        query = select(Company).where(Company.workspace_id == workspace_id, Company.id.in_(ids))
        return list(await self.session.scalars(query))

    async def get(self, workspace_id: UUID, company_id: UUID) -> Company | None:
        return await self.session.scalar(select(Company).where(Company.workspace_id == workspace_id, Company.id == company_id))

    async def matcher(self, workspace_id: UUID):
        """A function finding the pipeline company for a domain or name, from one read of the pipeline.
        The domain is tried first; the name only when it's unique in the workspace."""
        rows = list(await self.session.scalars(select(Company).where(Company.workspace_id == workspace_id)))
        by_domain = {c.domain: c for c in rows if c.domain}
        names: dict[str, list[Company]] = {}
        for c in rows:
            names.setdefault(c.name.strip().lower(), []).append(c)

        def find(domain: str | None, name: str | None) -> Company | None:
            if domain and domain in by_domain:
                return by_domain[domain]
            same = names.get((name or "").strip().lower(), [])
            return same[0] if len(same) == 1 else None

        return find

    async def save_pages(self, workspace_id: UUID, company_id: UUID, via: str, pages: list[dict[str, Any]]) -> None:
        """Store the pages read from a company's website; a page read again replaces its old text."""
        for page in pages:
            values = {"role": page["role"], "via": via, "status": page.get("status"), "text": page.get("text") or ""}
            await self.session.execute(
                insert(CompanyPage)
                .values(workspace_id=workspace_id, company_id=company_id, url=page["url"], **values)
                .on_conflict_do_update(
                    index_elements=[CompanyPage.company_id, CompanyPage.url], set_={**values, "fetched_at": func.now()}
                )
            )
        await self.session.commit()

    async def pages(self, workspace_id: UUID, company_id: UUID) -> list[CompanyPage]:
        query = select(CompanyPage).where(CompanyPage.workspace_id == workspace_id, CompanyPage.company_id == company_id)
        return list(await self.session.scalars(query.order_by(CompanyPage.fetched_at, CompanyPage.url)))

    async def list(self, workspace_id: UUID, icp_id: UUID | None, status: str | None) -> list[Company]:
        query = select(Company).where(Company.workspace_id == workspace_id)
        if icp_id:
            query = query.where(Company.icp_id == icp_id)
        if status:
            query = query.where(Company.status == status)
        query = query.order_by(Company.score.desc().nulls_last(), Company.created_at.desc(), Company.id)
        return list(await self.session.scalars(query))

    async def _by_keys(self, workspace_id: UUID) -> tuple[dict[UUID, Company], dict[str, Company], dict[str, Company]]:
        rows = await self.session.scalars(select(Company).where(Company.workspace_id == workspace_id))
        by_business: dict[UUID, Company] = {}
        by_apollo: dict[str, Company] = {}
        by_domain: dict[str, Company] = {}
        for row in rows:
            if row.lead_intel_business_id:
                by_business[row.lead_intel_business_id] = row
            if row.apollo_org_id:
                by_apollo[row.apollo_org_id] = row
            if row.domain:
                by_domain[row.domain] = row
        return by_business, by_apollo, by_domain

    async def add_lead_intel(self, workspace_id: UUID, icp_id: UUID, companies: list[dict[str, Any]]) -> tuple[int, int, int]:
        """Add Lead Intel companies, qualified straight away (Lead Intel already verified them).

        Returns (added, upgraded, already_had). A company already here from Apollo with the same
        domain becomes the Lead Intel one rather than a second row.
        """
        by_business, _, by_domain = await self._by_keys(workspace_id)
        added = upgraded = had = 0
        for c in companies:
            if c["id"] in by_business:
                had += 1
                continue
            existing = by_domain.get(c["domain"]) if c.get("domain") else None
            if existing is not None:
                existing.source = "lead_intel"
                existing.lead_intel_business_id = c["id"]
                existing.instagram_url = existing.instagram_url or c.get("instagram_url")
                if not existing.overridden:
                    existing.status = "qualified"
                    existing.status_reason = "In Lead Intel, which already verified it"
                by_business[c["id"]] = existing
                upgraded += 1
                continue
            row = Company(
                workspace_id=workspace_id,
                icp_id=icp_id,
                source="lead_intel",
                lead_intel_business_id=c["id"],
                name=c["name"],
                domain=c.get("domain"),
                website_url=c.get("website_url"),
                instagram_url=c.get("instagram_url"),
                city=c.get("city"),
                state=c.get("state"),
                status="qualified",
                status_reason="In Lead Intel, which already verified it",
            )
            self.session.add(row)
            if row.domain:
                by_domain[row.domain] = row
            by_business[c["id"]] = row
            added += 1
        await self.session.commit()
        return added, upgraded, had

    async def add_apollo(self, workspace_id: UUID, icp_id: UUID, orgs: list[ApolloOrganization]) -> tuple[int, int]:
        """Add Apollo companies to be researched. Returns (added, duplicates): the same Apollo id,
        or the same domain as a company already here from either source, is a duplicate."""
        _, by_apollo, by_domain = await self._by_keys(workspace_id)
        added = dupes = 0
        for org in orgs:
            if org.apollo_id in by_apollo or (org.domain and org.domain in by_domain):
                dupes += 1
                continue
            row = Company(
                workspace_id=workspace_id,
                icp_id=icp_id,
                source="apollo",
                apollo_org_id=org.apollo_id,
                name=org.name,
                domain=org.domain,
                website_url=org.website_url,
                linkedin_url=org.linkedin_url,
                facebook_url=org.facebook_url,
                twitter_url=org.twitter_url,
                status="found",
                raw=org.raw,
            )
            self.session.add(row)
            by_apollo[org.apollo_id] = row
            if org.domain:
                by_domain[org.domain] = row
            added += 1
        await self.session.commit()
        return added, dupes

    async def save(self, company: Company) -> None:
        await self.session.commit()
        await self.session.refresh(company)


class EmailRepo:
    """Drafted emails and the ranked points behind them. `outreach.eo_emails`, `eo_insights`, and the lead's status."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def leads(self, workspace_id: UUID, lead_ids: list[UUID]) -> list[Lead]:
        return list(await self.session.scalars(select(Lead).where(Lead.workspace_id == workspace_id, Lead.id.in_(lead_ids))))

    async def company_for(self, workspace_id: UUID, lead: Lead) -> Company | None:
        """The pipeline company the lead was found at, else the one matching their Lead Intel company."""
        company = await self.session.scalar(
            select(Company)
            .join(Stakeholder, Stakeholder.company_id == Company.id)
            .where(Stakeholder.workspace_id == workspace_id, Stakeholder.lead_id == lead.id)
            .limit(1)
        )
        if company is None and lead.lead_intel_business_id:
            company = await self.session.scalar(
                select(Company).where(
                    Company.workspace_id == workspace_id, Company.lead_intel_business_id == lead.lead_intel_business_id
                )
            )
        return company

    async def posts_for(self, workspace_id: UUID, lead: Lead, company: Company | None, limit: int) -> list[Post]:
        """The lead's own posts and their company's, newest first."""
        owners = [Post.lead_id == lead.id] + ([Post.company_id == company.id] if company else [])
        query = (
            select(Post)
            .where(Post.workspace_id == workspace_id, or_(*owners))
            .order_by(Post.posted_at.desc().nulls_last(), Post.id)
            .limit(limit)
        )
        return list(await self.session.scalars(query))

    async def save_draft(
        self, workspace_id: UUID, lead: Lead, *, points: list[dict[str, Any]], email: dict[str, Any]
    ) -> Email:
        """Replace the lead's points and unsent draft with new ones; the lead becomes 'generated'."""
        await self.session.execute(delete(Insight).where(Insight.lead_id == lead.id))
        for rank, point in enumerate(points, 1):
            self.session.add(Insight(workspace_id=workspace_id, lead_id=lead.id, rank=rank, **point))
        await self.session.execute(delete(Email).where(Email.lead_id == lead.id, Email.status.in_(("draft", "edited"))))
        row = Email(workspace_id=workspace_id, lead_id=lead.id, status="draft", **email)
        self.session.add(row)
        lead.status = "generated"
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list(
        self, workspace_id: UUID, status: str | None = None, lead_id: UUID | None = None
    ) -> list[tuple[Email, Lead]]:
        query = select(Email, Lead).join(Lead, Lead.id == Email.lead_id).where(Email.workspace_id == workspace_id)
        if status:
            query = query.where(Email.status == status)
        if lead_id:
            query = query.where(Email.lead_id == lead_id)
        return [(e, lead) for e, lead in await self.session.execute(query.order_by(Email.created_at.desc()))]

    async def get(self, workspace_id: UUID, email_id: UUID) -> tuple[Email, Lead] | None:
        row = (
            await self.session.execute(
                select(Email, Lead).join(Lead, Lead.id == Email.lead_id).where(
                    Email.workspace_id == workspace_id, Email.id == email_id
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def insights(self, lead_ids: list[UUID]) -> dict[UUID, list[Insight]]:
        out: dict[UUID, list[Insight]] = {}
        if lead_ids:
            rows = await self.session.scalars(select(Insight).where(Insight.lead_id.in_(lead_ids)).order_by(Insight.rank))
            for i in rows:
                out.setdefault(i.lead_id, []).append(i)
        return out

    async def posts(self, post_ids: list[UUID]) -> dict[UUID, Post]:
        if not post_ids:
            return {}
        return {p.id: p for p in await self.session.scalars(select(Post).where(Post.id.in_(post_ids)))}

    async def commit_refresh(self, *rows: Any) -> None:
        await self.session.commit()
        for row in rows:
            await self.session.refresh(row)


class FeedbackRepo:
    """People's ratings of generated emails. `outreach.eo_email_feedback` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_emails(self, email_ids: list[UUID]) -> dict[UUID, EmailFeedback]:
        if not email_ids:
            return {}
        rows = await self.session.scalars(select(EmailFeedback).where(EmailFeedback.email_id.in_(email_ids)))
        return {f.email_id: f for f in rows}

    async def rate(self, email: Email, lead: Lead, rating: str, tags: list[str], note: str) -> EmailFeedback:
        """Rate an email (again): the text is snapshotted as it is now."""
        values = {
            "lead_intel_icp_id": lead.lead_intel_icp_id, "rating": rating, "tags": tags, "note": note,
            "subject": email.subject, "body": email.edited_body or email.body, "hook": email.hook,
        }  # fmt: skip
        row = (
            await self.session.scalars(
                insert(EmailFeedback)
                .values(workspace_id=email.workspace_id, email_id=email.id, **values)
                .on_conflict_do_update(index_elements=[EmailFeedback.email_id], set_={**values, "updated_at": func.now()})
                .returning(EmailFeedback)
            )
        ).one()
        await self.session.commit()
        return row

    async def clear(self, email_id: UUID) -> None:
        await self.session.execute(delete(EmailFeedback).where(EmailFeedback.email_id == email_id))
        await self.session.commit()

    async def examples(self, workspace_id: UUID, icp_id: UUID, good: int, bad: int) -> list[EmailFeedback]:
        """The ICP's most recent good ratings (up to `good`) and bad ones (up to `bad`)."""
        out: list[EmailFeedback] = []
        for rating, limit in (("good", good), ("bad", bad)):
            if limit <= 0:
                continue
            out += await self.session.scalars(
                select(EmailFeedback)
                .where(
                    EmailFeedback.workspace_id == workspace_id,
                    EmailFeedback.lead_intel_icp_id == icp_id,
                    EmailFeedback.rating == rating,
                )
                .order_by(EmailFeedback.updated_at.desc())
                .limit(limit)
            )
        return out

    async def stats(self, workspace_id: UUID) -> list[tuple[UUID | None, str, int]]:
        rows = await self.session.execute(
            select(EmailFeedback.lead_intel_icp_id, EmailFeedback.rating, func.count())
            .where(EmailFeedback.workspace_id == workspace_id)
            .group_by(EmailFeedback.lead_intel_icp_id, EmailFeedback.rating)
        )
        return [(icp, rating, n) for icp, rating, n in rows]


FEEDBACK_DEFAULTS = {"good_examples": 3, "bad_examples": 3, "improve_examples": 20}
RESEARCH_DEFAULTS = {"research_max_pages": 6, "research_max_seconds": 120}


class SettingsRepo:
    """The workspace's settings row (`outreach.eo_settings`). Only the fields this app uses so far."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workspace_exists(self, workspace_id: UUID) -> bool:
        return (await self.session.get(Workspace, workspace_id)) is not None

    async def _get(self, workspace_id: UUID, defaults: dict[str, int]) -> dict[str, int]:
        """These settings, or their defaults while the workspace has no settings row yet."""
        row = await self.session.get(Settings, workspace_id)
        return dict(defaults) if row is None else {k: getattr(row, k) for k in defaults}

    async def _save(self, workspace_id: UUID, values: dict[str, int]) -> None:
        await self.session.execute(
            insert(Settings)
            .values(workspace_id=workspace_id, **values)
            .on_conflict_do_update(index_elements=[Settings.workspace_id], set_={**values, "updated_at": func.now()})
        )
        await self.session.commit()
        # The row may have been read before this save; read it fresh next time.
        self.session.expire_all()

    async def feedback(self, workspace_id: UUID) -> dict[str, int]:
        """How many rated emails are used."""
        return await self._get(workspace_id, FEEDBACK_DEFAULTS)

    async def save_feedback(self, workspace_id: UUID, values: dict[str, int]) -> dict[str, int]:
        await self._save(workspace_id, values)
        return await self.feedback(workspace_id)

    async def research(self, workspace_id: UUID) -> dict[str, int]:
        """How much of a company's website research may read."""
        return await self._get(workspace_id, RESEARCH_DEFAULTS)

    async def save_research(self, workspace_id: UUID, values: dict[str, int]) -> dict[str, int]:
        await self._save(workspace_id, values)
        return await self.research(workspace_id)
