from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from config import get_settings
from crud import (
    CompanyRepo,
    IcpProfileRepo,
    LeadRepo,
    PostRepo,
    ProviderCallRepo,
    StakeholderRepo,
    WorkspaceCompanyRepo,
    WorkspaceRepo,
)
from database import get_lead_intel_icp_conn, get_readonly_conn, get_session
from lead_intel import LeadIntelRepo
from lead_intel_writes import LeadIntelIcpWriter
from services.apify import ApifyClient
from services.apollo import ApolloClient
from services.jina import JinaClient
from services.scoring import CompanyScorer
from services.website import WebsiteReader


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


def get_lead_intel_icp_writer(conn: AsyncConnection = Depends(get_lead_intel_icp_conn)) -> LeadIntelIcpWriter:
    return LeadIntelIcpWriter(conn)


def get_post_repo(session: AsyncSession = Depends(get_session)) -> PostRepo:
    return PostRepo(session)


async def get_apify() -> AsyncIterator[ApifyClient]:
    token = get_settings().apify_token
    if not token:
        raise HTTPException(status_code=503, detail="APIFY_TOKEN is not set")
    client = ApifyClient(token)
    try:
        yield client
    finally:
        await client.aclose()


def get_company_repo(session: AsyncSession = Depends(get_session)) -> CompanyRepo:
    return CompanyRepo(session)


async def get_optional_apollo() -> AsyncIterator[ApolloClient | None]:
    key = get_settings().apollo_api_key
    client = ApolloClient(key) if key else None
    try:
        yield client
    finally:
        if client:
            await client.aclose()


async def get_optional_apify() -> AsyncIterator[ApifyClient | None]:
    token = get_settings().apify_token
    client = ApifyClient(token) if token else None
    try:
        yield client
    finally:
        if client:
            await client.aclose()


async def get_website_reader() -> AsyncIterator[WebsiteReader]:
    settings = get_settings()
    jina = JinaClient(settings.jina_api_key, settings.jina_rpm)
    reader = WebsiteReader(jina=jina)
    try:
        yield reader
    finally:
        await reader.aclose()
        await jina.aclose()


async def get_optional_scorer() -> AsyncIterator[CompanyScorer | None]:
    settings = get_settings()
    scorer = CompanyScorer(settings.anthropic_api_key, settings.claude_scoring_model) if settings.anthropic_api_key else None
    try:
        yield scorer
    finally:
        if scorer:
            await scorer.aclose()
