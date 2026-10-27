from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import WorkspaceRepo
from dependencies import get_workspace_repo
from schemas import WorkspaceIn, WorkspaceOut, WorkspaceUpdate

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


async def _get_or_404(workspaces: WorkspaceRepo, workspace_id: UUID):
    workspace = await workspaces.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(workspaces: WorkspaceRepo = Depends(get_workspace_repo)) -> list[WorkspaceOut]:
    return [WorkspaceOut.model_validate(w) for w in await workspaces.list()]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceIn, workspaces: WorkspaceRepo = Depends(get_workspace_repo)) -> WorkspaceOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the workspace a name")
    if await workspaces.name_taken(name):
        raise HTTPException(status_code=409, detail="A workspace with this name already exists")
    return WorkspaceOut.model_validate(await workspaces.create(name, body.description.strip()))


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: UUID, body: WorkspaceUpdate, workspaces: WorkspaceRepo = Depends(get_workspace_repo)
) -> WorkspaceOut:
    workspace = await _get_or_404(workspaces, workspace_id)
    values = {k: v.strip() for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in values:
        if not values["name"]:
            raise HTTPException(status_code=422, detail="Give the workspace a name")
        if await workspaces.name_taken(values["name"], except_id=workspace_id):
            raise HTTPException(status_code=409, detail="A workspace with this name already exists")
    return WorkspaceOut.model_validate(await workspaces.update(workspace, values))


@router.delete("/{workspace_id}", status_code=204, response_class=Response)
async def delete_workspace(workspace_id: UUID, workspaces: WorkspaceRepo = Depends(get_workspace_repo)) -> Response:
    """Delete a workspace and everything it owns in our tables. Lead Intel is untouched."""
    workspace = await _get_or_404(workspaces, workspace_id)
    if len(await workspaces.list()) <= 1:
        raise HTTPException(status_code=409, detail="It's the only workspace. Create another one first.")
    await workspaces.delete(workspace)
    return Response(status_code=204)
