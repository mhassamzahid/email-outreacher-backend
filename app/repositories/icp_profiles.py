from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IcpProfile


class IcpProfileRepo:
    """Reads and writes `outreach.icp_profiles` only."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(self, icp_ids: list[UUID]) -> dict[UUID, IcpProfile]:
        if not icp_ids:
            return {}
        result = await self.session.scalars(select(IcpProfile).where(IcpProfile.lead_intel_icp_id.in_(icp_ids)))
        return {p.lead_intel_icp_id: p for p in result}

    async def upsert(self, icp_id: UUID, values: dict) -> IcpProfile:
        statement = (
            insert(IcpProfile)
            .values(lead_intel_icp_id=icp_id, **values)
            .on_conflict_do_update(
                index_elements=[IcpProfile.lead_intel_icp_id],
                set_={**values, "updated_at": func.now()},
            )
            .returning(IcpProfile)
        )
        profile = (await self.session.scalars(statement)).one()
        await self.session.commit()
        return profile
