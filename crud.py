"""Reads and writes for our own `outreach.eo_` tables. Lead Intel is read in lead_intel.py, never here."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import IcpProfile, Lead, Workspace
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
