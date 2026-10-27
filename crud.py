"""Reads and writes for our own `outreach.eo_` tables. Lead Intel is read in lead_intel.py, never here."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import IcpProfile, Lead, Post, ProviderCall, Stakeholder, Workspace, WorkspaceCompany
from services.apify import Employee, FetchedPost
from services.apollo import FoundPerson, RevealedPerson
from services.leads import Existing, LeadInput


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
        self, workspace_id: UUID, company_id: UUID | None, people: list[FoundPerson], icp_id: UUID | None = None
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
                    "raw": case((Stakeholder.revealed_at.is_(None), statement.excluded.raw), else_=Stakeholder.raw),
                    "last_name": case(
                        (Stakeholder.revealed_at.is_(None), statement.excluded.last_name), else_=Stakeholder.last_name
                    ),
                },
            )
            await self.session.execute(statement)
        await self.session.commit()
        return sum(p.apollo_id not in known for p in people)

    async def save_employees(self, workspace_id: UUID, company_id: UUID, people: list[Employee]) -> int:
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
                },
            )
            await self.session.execute(statement)
        await self.session.commit()
        return sum(p.external_id not in known for p in people)

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
            )
            .on_conflict_do_nothing()
        )
        await self.session.commit()


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

    async def list(self, workspace_id: UUID, lead_id: UUID | None) -> list[Post]:
        query = select(Post).where(Post.workspace_id == workspace_id)
        if lead_id:
            query = query.where(Post.lead_id == lead_id)
        return list(await self.session.scalars(query.order_by(Post.posted_at.desc().nulls_last(), Post.id)))
