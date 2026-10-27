"""SELECT-only queries against Lead Intel's tables. Nothing in this module writes."""

from typing import Any
from uuid import UUID

from sqlalchemy import Text, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.lead_intel import businesses, field_metadata, icps, identity_keys

# The fields shown on every company row. Everything else is only returned on the detail view.
LIST_FIELDS = (
    "website_url",
    "instagram_handle",
    "instagram_url",
    "instagram_followers",
    "instagram_category",
    "instagram_bio",
)

# Lead Intel's status columns are Postgres enums; comparing them as text avoids enum-vs-varchar
# operator errors without having to declare the enum types here.
_contactable = and_(
    cast(businesses.c.status, Text) == "active",
    businesses.c.exclusion_flag.is_(False),
)


class LeadIntelRepo:
    def __init__(self, conn: AsyncConnection) -> None:
        self.conn = conn

    async def list_icps(self) -> list[dict[str, Any]]:
        counts = (
            select(businesses.c.icp_id, func.count().label("company_count"))
            .where(_contactable, businesses.c.icp_id.is_not(None))
            .group_by(businesses.c.icp_id)
            .subquery()
        )
        query = (
            select(icps, func.coalesce(counts.c.company_count, 0).label("company_count"))
            .outerjoin(counts, counts.c.icp_id == icps.c.id)
            .order_by(icps.c.priority, icps.c.created_at)
        )
        return [dict(row) for row in (await self.conn.execute(query)).mappings()]

    async def get_icp(self, icp_id: UUID) -> dict[str, Any] | None:
        count = (
            select(func.count())
            .where(_contactable, businesses.c.icp_id == icp_id)
            .scalar_subquery()
        )
        query = select(icps, count.label("company_count")).where(icps.c.id == icp_id)
        row = (await self.conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def list_companies(
        self, icp_id: UUID | None, q: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = [_contactable]
        if icp_id:
            where.append(businesses.c.icp_id == icp_id)
        if q:
            where.append(businesses.c.display_name.ilike(f"%{q}%"))

        total = (await self.conn.execute(select(func.count()).select_from(businesses).where(*where))).scalar_one()
        rows = (
            await self.conn.execute(
                select(businesses)
                .where(*where)
                .order_by(businesses.c.tier.asc().nulls_last(), businesses.c.problem_score.desc().nulls_last(), businesses.c.id)
                .limit(limit)
                .offset(offset)
            )
        ).mappings().all()

        companies = [dict(row) for row in rows]
        await self._attach_fields(companies, LIST_FIELDS)
        return companies, total

    async def get_company(self, company_id: UUID) -> dict[str, Any] | None:
        row = (
            await self.conn.execute(select(businesses).where(businesses.c.id == company_id, _contactable))
        ).mappings().first()
        if not row:
            return None
        company = dict(row)
        await self._attach_fields([company], None)
        return company

    async def _attach_fields(self, companies: list[dict[str, Any]], names: tuple[str, ...] | None) -> None:
        """Add each company's stored fields and its domain, in two queries for the whole page."""
        ids = [c["id"] for c in companies]
        for c in companies:
            c["fields"] = {}
            c["domain"] = None
        if not ids:
            return
        by_id = {c["id"]: c for c in companies}

        fields_query = select(field_metadata.c.business_id, field_metadata.c.field_name, field_metadata.c.value).where(
            field_metadata.c.business_id.in_(ids)
        )
        if names is not None:
            fields_query = fields_query.where(field_metadata.c.field_name.in_(names))
        for business_id, name, value in await self.conn.execute(fields_query):
            by_id[business_id]["fields"][name] = value

        domains = select(identity_keys.c.business_id, identity_keys.c.key_value).where(
            identity_keys.c.business_id.in_(ids), cast(identity_keys.c.key_type, Text) == "domain"
        )
        for business_id, domain in await self.conn.execute(domains):
            by_id[business_id]["domain"] = domain
