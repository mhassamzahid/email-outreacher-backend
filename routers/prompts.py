"""Prompt templates for this workspace: Company Verification, Hook Selection, Email Generation, and
the email scoring prompt in Settings. Each ICP picks one of the first three, or uses the default.

Saving a prompt adds a version; versions are never edited. Every workspace starts with one
built-in default of each kind (services/prompts.py).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import PromptRepo
from dependencies import get_prompt_repo, require_workspace_id
from schemas import (
    NewVersionIn,
    PromptCreateIn,
    PromptOut,
    PromptsOut,
    PromptUpdateIn,
    PromptVersionOut,
    PromptWithVersion,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])


async def _get(prompts: PromptRepo, workspace_id: UUID, prompt_id: UUID):
    prompt = await prompts.get(workspace_id, prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return prompt


async def _workspace(prompts: PromptRepo, workspace_id: UUID) -> None:
    if not await prompts.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.get("", response_model=PromptsOut)
async def list_prompts(
    workspace_id: UUID = Depends(require_workspace_id), prompts: PromptRepo = Depends(get_prompt_repo)
) -> PromptsOut:
    """Every prompt in the workspace with all its versions. Creates the built-in defaults if missing."""
    await _workspace(prompts, workspace_id)
    await prompts.ensure_defaults(workspace_id)
    rows, versions = await prompts.list(workspace_id)
    return PromptsOut(
        prompts=[PromptOut.model_validate(p) for p in rows],
        versions=[PromptVersionOut.model_validate(v) for v in versions],
    )


@router.post("", response_model=PromptWithVersion, status_code=201)
async def create_prompt(
    body: PromptCreateIn,
    workspace_id: UUID = Depends(require_workspace_id),
    prompts: PromptRepo = Depends(get_prompt_repo),
) -> PromptWithVersion:
    await _workspace(prompts, workspace_id)
    prompt, version = await prompts.create(workspace_id, body.name.strip(), body.kind, body.version.model_dump(), body.note)
    if body.is_default:
        await prompts.make_default(prompt)
    return PromptWithVersion(prompt=PromptOut.model_validate(prompt), version=PromptVersionOut.model_validate(version))


@router.post("/{prompt_id}/versions", response_model=PromptVersionOut, status_code=201)
async def save_version(
    prompt_id: UUID,
    body: NewVersionIn,
    workspace_id: UUID = Depends(require_workspace_id),
    prompts: PromptRepo = Depends(get_prompt_repo),
) -> PromptVersionOut:
    """Save the prompt as its next version, which becomes the one in use."""
    prompt = await _get(prompts, workspace_id, prompt_id)
    version = await prompts.add_version(prompt, body.model_dump(exclude={"note"}), body.note)
    return PromptVersionOut.model_validate(version)


@router.patch("/{prompt_id}", response_model=PromptOut)
async def update_prompt(
    prompt_id: UUID,
    body: PromptUpdateIn,
    workspace_id: UUID = Depends(require_workspace_id),
    prompts: PromptRepo = Depends(get_prompt_repo),
) -> PromptOut:
    """Rename it, or make it the default of its kind (the old default stops being one)."""
    prompt = await _get(prompts, workspace_id, prompt_id)
    if body.name is not None:
        await prompts.rename(prompt, body.name.strip())
    if body.is_default:
        await prompts.make_default(prompt)
    return PromptOut.model_validate(prompt)


@router.delete("/{prompt_id}", status_code=204)
async def delete_prompt(
    prompt_id: UUID,
    workspace_id: UUID = Depends(require_workspace_id),
    prompts: PromptRepo = Depends(get_prompt_repo),
) -> Response:
    """Delete a prompt and its versions. Not the default, and not one an ICP uses."""
    prompt = await _get(prompts, workspace_id, prompt_id)
    if prompt.is_default:
        raise HTTPException(status_code=409, detail="This is the default prompt. Make another one the default first.")
    if used := await prompts.in_use(prompt):
        raise HTTPException(
            status_code=409, detail=f"{used} {'ICP uses' if used == 1 else 'ICPs use'} this prompt. Switch them first."
        )
    await prompts.delete(prompt)
    return Response(status_code=204)
