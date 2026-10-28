"""Workspace settings: how many rated emails Claude learns from, and how much of a website research reads."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from crud import SettingsRepo
from dependencies import get_settings_repo, require_workspace_id
from schemas import FeedbackSettings, ResearchSettings

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
