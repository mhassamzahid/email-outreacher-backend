"""Workspace settings: how many rated emails Claude learns from, how much of a website research reads,
and the cost estimator."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from crud import SettingsRepo
from dependencies import get_settings_repo, require_manager, require_workspace_id
from schemas import CostEstimate, FeedbackSettings, ResearchSettings
from services.auth import CurrentUser

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


@router.get("/cost-estimate", response_model=CostEstimate)
async def get_cost_estimate(
    workspace_id: UUID = Depends(require_workspace_id), settings: SettingsRepo = Depends(get_settings_repo)
) -> CostEstimate:
    """The cost estimator's inputs and cost items: the workspace's saved ones, or the starting ones."""
    await _workspace(settings, workspace_id)
    return CostEstimate(**await settings.cost_estimate(workspace_id))


@router.put("/cost-estimate", response_model=CostEstimate)
async def save_cost_estimate(
    body: CostEstimate,
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
    _: CurrentUser = Depends(require_manager),
) -> CostEstimate:
    await _workspace(settings, workspace_id)
    if len({item.id for item in body.items}) != len(body.items):
        raise HTTPException(status_code=422, detail="Each cost item needs its own id")
    return CostEstimate(**await settings.save_cost_estimate(workspace_id, body.model_dump()))
