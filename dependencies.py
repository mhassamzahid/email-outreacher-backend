from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from config import get_settings
from crud import IcpProfileRepo, LeadRepo, ProviderCallRepo, StakeholderRepo, WorkspaceCompanyRepo, WorkspaceRepo
from database import get_readonly_conn, get_session
from lead_intel import LeadIntelRepo
from services.apollo import ApolloClient


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


def get_lead_repo(session: AsyncSession = Depends(get_session)) -> LeadRepo:
    return LeadRepo(session)


def get_workspace_company_repo(session: AsyncSession = Depends(get_session)) -> WorkspaceCompanyRepo:
    return WorkspaceCompanyRepo(session)


def get_workspace_repo(session: AsyncSession = Depends(get_session)) -> WorkspaceRepo:
    return WorkspaceRepo(session)


def get_stakeholder_repo(session: AsyncSession = Depends(get_session)) -> StakeholderRepo:
    return StakeholderRepo(session)


def get_provider_call_repo(session: AsyncSession = Depends(get_session)) -> ProviderCallRepo:
    return ProviderCallRepo(session)


async def get_apollo() -> AsyncIterator[ApolloClient]:
    key = get_settings().apollo_api_key
    if not key:
        raise HTTPException(status_code=503, detail="APOLLO_API_KEY is not set")
    client = ApolloClient(key)
    try:
        yield client
    finally:
        await client.aclose()
