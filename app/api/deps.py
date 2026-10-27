from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.session import get_readonly_conn, get_session
from app.repositories.icp_profiles import IcpProfileRepo
from app.repositories.lead_intel import LeadIntelRepo


def get_lead_intel_repo(conn: AsyncConnection = Depends(get_readonly_conn)) -> LeadIntelRepo:
    return LeadIntelRepo(conn)


def get_workspace_id(x_workspace_id: UUID | None = Header(default=None)) -> UUID | None:
    """The workspace the request is for. Until sign-in exists, the frontend sends it as a header."""
    return x_workspace_id


def require_workspace_id(workspace_id: UUID | None = Depends(get_workspace_id)) -> UUID:
    if workspace_id is None:
        raise HTTPException(status_code=400, detail="X-Workspace-Id header is required")
    return workspace_id


def get_icp_profile_repo(session: AsyncSession = Depends(get_session)) -> IcpProfileRepo:
    return IcpProfileRepo(session)
