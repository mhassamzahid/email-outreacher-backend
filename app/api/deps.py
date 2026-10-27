from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.session import get_readonly_conn, get_session
from app.repositories.icp_profiles import IcpProfileRepo
from app.repositories.lead_intel import LeadIntelRepo


def get_lead_intel_repo(conn: AsyncConnection = Depends(get_readonly_conn)) -> LeadIntelRepo:
    return LeadIntelRepo(conn)


def get_icp_profile_repo(session: AsyncSession = Depends(get_session)) -> IcpProfileRepo:
    return IcpProfileRepo(session)
