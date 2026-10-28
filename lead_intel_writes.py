"""The only writes this service makes to Lead Intel: adding an ICP, and editing an ICP's fields.

Agreed scope, enforced by tests/test_lead_intel_safety.py:
  - public.icps only: insert a new ICP (agreed first), and update an existing ICP's own fields
    (agreed 2026-10-28, for every ICP). Never a delete, and no other Lead Intel table.
  - All other Lead Intel data (businesses, their fields, everything else) is never changed.
Everything else that touches Lead Intel lives in lead_intel.py and only reads.

Lead Intel decides which businesses belong to an ICP when it next runs its own matching; adding
an ICP here doesn't assign any businesses to it.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from lead_intel import icps


class LeadIntelIcpWriter:
    def __init__(self, conn: AsyncConnection) -> None:
        self.conn = conn

    async def name_taken(self, name: str, except_id: UUID | None = None) -> bool:
        query = select(icps.c.id).where(func.lower(icps.c.name) == name.lower())
        if except_id is not None:
            query = query.where(icps.c.id != except_id)
        return (await self.conn.execute(query.limit(1))).first() is not None

    async def add_icp(self, created_by: UUID, values: dict[str, Any]) -> dict[str, Any]:
        """Insert one new ICP, commit it, and return it as stored."""
        statement = insert(icps).values(created_by=created_by, **values).returning(*icps.c)
        row = dict((await self.conn.execute(statement)).mappings().one())
        await self.conn.commit()
        return row

    async def update_icp(self, icp_id: UUID, values: dict[str, Any]) -> dict[str, Any] | None:
        """Change one existing ICP's own fields, commit, and return it as stored (None if it doesn't exist).

        Only the columns in `values`; never its id or creator, never another ICP or table. Lead Intel
        re-assigns businesses to it when it next runs its own matching.
        """
        statement = (
            update(icps)
            .where(icps.c.id == icp_id)
            .values(**values, updated_at=func.now())
            .returning(*icps.c)
        )
        row = (await self.conn.execute(statement)).mappings().first()
        await self.conn.commit()
        return dict(row) if row else None
