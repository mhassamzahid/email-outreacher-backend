"""Workspace settings: how many rated emails Claude learns from, how much of a website research reads,
and the cost estimator."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from config import get_settings
from crud import ProviderCallRepo, SettingsRepo
from dependencies import get_optional_apify, get_provider_call_repo, get_settings_repo, require_manager, require_workspace_id
from schemas import ClaudePrice, CostEstimate, CostReference, FeedbackSettings, MeasuredUse, ResearchSettings
from services.auth import CurrentUser
from services.apify import ApifyClient, ApifyError
from services.claude import PRICES
from services.cost_estimate import refresh
from services.sources import apify_prices

router = APIRouter(prefix="/settings", tags=["settings"])


async def _workspace(settings: SettingsRepo, workspace_id: UUID) -> None:
    if not await settings.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.get("/feedback", response_model=FeedbackSettings)
async def get_feedback_settings(
    workspace_id: UUID = Depends(require_workspace_id), settings: SettingsRepo = Depends(get_settings_repo)
) -> FeedbackSettings:
    await _workspace(settings, workspace_id)
    return FeedbackSettings(**await settings.feedback(workspace_id))


@router.put("/feedback", response_model=FeedbackSettings)
async def save_feedback_settings(
    body: FeedbackSettings,
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
) -> FeedbackSettings:
    await _workspace(settings, workspace_id)
    return FeedbackSettings(**await settings.save_feedback(workspace_id, body.model_dump()))


@router.get("/research", response_model=ResearchSettings)
async def get_research_settings(
    workspace_id: UUID = Depends(require_workspace_id), settings: SettingsRepo = Depends(get_settings_repo)
) -> ResearchSettings:
    await _workspace(settings, workspace_id)
    return ResearchSettings(**await settings.research(workspace_id))


@router.put("/research", response_model=ResearchSettings)
async def save_research_settings(
    body: ResearchSettings,
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
) -> ResearchSettings:
    await _workspace(settings, workspace_id)
    return ResearchSettings(**await settings.save_research(workspace_id, body.model_dump()))


async def _priced(estimate: dict, apify: ApifyClient | None) -> dict:
    config = get_settings()
    prices = None
    if apify is not None:
        try:
            prices = (await apify_prices(apify))[1]
        except ApifyError:
            prices = None  # Apify unreachable: the starting prices stand in
    return refresh(estimate, config.claude_scoring_model, config.cost_price_overrides, prices)


@router.get("/cost-estimate", response_model=CostEstimate)
async def get_cost_estimate(
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
    apify: ApifyClient | None = Depends(get_optional_apify),
) -> CostEstimate:
    """The cost estimator's inputs and cost items: the workspace's saved ones, or the starting ones."""
    await _workspace(settings, workspace_id)
    return CostEstimate(**await _priced(await settings.cost_estimate(workspace_id), apify))


@router.put("/cost-estimate", response_model=CostEstimate)
async def save_cost_estimate(
    body: CostEstimate,
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
    apify: ApifyClient | None = Depends(get_optional_apify),
    _: CurrentUser = Depends(require_manager),
) -> CostEstimate:
    await _workspace(settings, workspace_id)
    if len({item.id for item in body.items}) != len(body.items):
        raise HTTPException(status_code=422, detail="Each cost item needs its own id")
    # Prices are read-only: what's saved is today's price, whatever the browser sent.
    priced = await _priced(body.model_dump(), apify)
    return CostEstimate(**await _priced(await settings.save_cost_estimate(workspace_id, priced), apify))


MEASURED_DAYS = 90


@router.get("/cost-estimate/reference", response_model=CostReference)
async def cost_reference(
    workspace_id: UUID = Depends(require_workspace_id), calls: ProviderCallRepo = Depends(get_provider_call_repo)
) -> CostReference:
    """What the estimator prices Claude with: each model's price per token, and the workspace's measured
    tokens per use of each Claude operation over the last 90 days."""
    since = datetime.now(UTC) - timedelta(days=MEASURED_DAYS)
    return CostReference(
        claude_prices=[ClaudePrice(model=m, input_per_mtok=p_in, output_per_mtok=p_out) for m, (p_in, p_out) in PRICES.items()],
        scoring_model=get_settings().claude_scoring_model,
        measured=[MeasuredUse(**m) for m in await calls.claude_per_use(workspace_id, since)],
        since=since,
    )
