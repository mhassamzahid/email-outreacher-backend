"""The one write this service makes to Lead Intel: adding a new ICP.

Agreed scope, enforced by tests/test_lead_intel_safety.py:
  - Inserts into public.icps only. Never an update or a delete, and no other Lead Intel table.
  - Existing ICPs and all other Lead Intel data are never changed.
Everything else that touches Lead Intel lives in lead_intel.py and only reads.

Lead Intel decides which businesses belong to an ICP when it next runs its own matching; adding
an ICP here doesn't assign any businesses to it.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from lead_intel import icps


class LeadIntelIcpWriter:
    def __init__(self, conn: AsyncConnection) -> None:
        self.conn = conn

    async def name_taken(self, name: str) -> bool:
        query = select(icps.c.id).where(func.lower(icps.c.name) == name.lower()).limit(1)
        return (await self.conn.execute(query)).first() is not None

    async def add_icp(self, created_by: UUID, values: dict[str, Any]) -> dict[str, Any]:
        """Insert one new ICP, commit it, and return it as stored."""
        statement = insert(icps).values(created_by=created_by, **values).returning(*icps.c)
        row = dict((await self.conn.execute(statement)).mappings().one())
        await self.conn.commit()
        return row
