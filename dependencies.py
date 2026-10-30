from collections.abc import AsyncIterator

import anthropic
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from config import get_settings
from crud import (
    CompanyRepo,
    EmailRepo,
    ExampleRepo,
    ExclusionRepo,
    FeedbackRepo,
    IcpProfileRepo,
    LeadRepo,
    PostRepo,
    ProductRepo,
    PromptRepo,
    SendingRepo,
    SettingsRepo,
    ProviderCallRepo,
    StakeholderRepo,
    UserRepo,
    WorkspaceCompanyRepo,
    WorkspaceRepo,
)
from database import get_lead_intel_icp_conn, get_readonly_conn, get_session
from lead_intel import LeadIntelRepo
from lead_intel_writes import LeadIntelIcpWriter
from services.apify import ApifyClient
from services.apollo import ApolloClient
from services.auth import AuthError, CurrentUser, verify
from services.generation import EmailWriter
from services.google_places import PlacesClient
from services.jina import JinaClient
from services.scoring import CompanyScorer
from services.sending import InboxesClient, SequencerClient
from services.website import WebsiteReader


def get_lead_intel_repo(conn: AsyncConnection = Depends(get_readonly_conn)) -> LeadIntelRepo:
    return LeadIntelRepo(conn)


def get_sending_repo(session: AsyncSession = Depends(get_session)) -> SendingRepo:
    return SendingRepo(session)


def get_exclusion_repo(session: AsyncSession = Depends(get_session)) -> ExclusionRepo:
    return ExclusionRepo(session)


def get_user_repo(session: AsyncSession = Depends(get_session)) -> UserRepo:
    return UserRepo(session)


async def get_current_user(
    authorization: str | None = Header(default=None), users: UserRepo = Depends(get_user_repo)
) -> CurrentUser:
    """The signed-in person, from the Supabase access token in `Authorization: Bearer`. Every API
    route except health depends on this."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    settings = get_settings()
    try:
        claims = await verify(token, settings.jwks_url, settings.auth_issuer)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc
    user = await users.for_sign_in(claims.auth_user_id, claims.email)
    if user.status == "deactivated":
        raise HTTPException(status_code=403, detail="Your account is deactivated. Ask an admin to turn it back on.")
    return CurrentUser(user.id, user.email, user.name, user.role, await users.workspace_ids(user.id))


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can do that.")
    return user


def require_manager(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Admin or Management: for changes shared beyond one workspace, like Lead Intel's ICPs."""
    if user.role not in ("admin", "management"):
        raise HTTPException(status_code=403, detail="Only Management and admins can change ICPs.")
    return user


def get_workspace_id(
    x_workspace_id: UUID | None = Header(default=None), user: CurrentUser = Depends(get_current_user)
) -> UUID | None:
    """The workspace the request is for, sent by the frontend as a header. Refused when the signed-in
    person can't open it; admins open every workspace."""
    if x_workspace_id is not None and not user.can_open(x_workspace_id):
        raise HTTPException(status_code=403, detail="You don't have access to this workspace. Ask an admin to add you.")
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


def get_email_repo(session: AsyncSession = Depends(get_session)) -> EmailRepo:
    return EmailRepo(session)


async def get_optional_writer() -> AsyncIterator[EmailWriter | None]:
    """The email writer, or None when ANTHROPIC_API_KEY isn't set."""
    key = get_settings().anthropic_api_key
    if not key:
        yield None
        return
    writer = EmailWriter(key)
    try:
        yield writer
    finally:
        await writer.aclose()


def get_feedback_repo(session: AsyncSession = Depends(get_session)) -> FeedbackRepo:
    return FeedbackRepo(session)


async def get_optional_anthropic() -> AsyncIterator[anthropic.AsyncAnthropic | None]:
    key = get_settings().anthropic_api_key
    if not key:
        yield None
        return
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=4, timeout=300.0)
    try:
        yield client
    finally:
        await client.close()


def get_settings_repo(session: AsyncSession = Depends(get_session)) -> SettingsRepo:
    return SettingsRepo(session)


def get_product_repo(session: AsyncSession = Depends(get_session)) -> ProductRepo:
    return ProductRepo(session)


def get_example_repo(session: AsyncSession = Depends(get_session)) -> ExampleRepo:
    return ExampleRepo(session)


def get_prompt_repo(session: AsyncSession = Depends(get_session)) -> PromptRepo:
    return PromptRepo(session)


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


async def get_optional_places() -> AsyncIterator[PlacesClient | None]:
    key = get_settings().google_places_api_key
    client = PlacesClient(key) if key else None
    try:
        yield client
    finally:
        if client:
            await client.aclose()


async def get_optional_sequencer() -> AsyncIterator[SequencerClient | None]:
    key = get_settings().cheapsequencer_api_key
    client = SequencerClient(key) if key else None
    try:
        yield client
    finally:
        if client:
            await client.aclose()


async def get_optional_inboxes() -> AsyncIterator[InboxesClient | None]:
    key = get_settings().cheapinboxes_api_key
    client = InboxesClient(key) if key else None
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
