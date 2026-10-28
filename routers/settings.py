"""Workspace settings. So far: how many rated emails Claude learns from."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from crud import SettingsRepo
from dependencies import get_settings_repo, require_workspace_id
from schemas import FeedbackSettings

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
