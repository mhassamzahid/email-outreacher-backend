from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import UserRepo, WorkspaceRepo
from dependencies import forget_sign_ins, get_current_user, get_user_repo, get_workspace_repo, require_admin
from schemas import WorkspaceIn, WorkspaceMembersIn, WorkspaceOut, WorkspaceUpdate
from services.auth import CurrentUser

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


async def _get_or_404(workspaces: WorkspaceRepo, workspace_id: UUID):
    workspace = await workspaces.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: CurrentUser = Depends(get_current_user), workspaces: WorkspaceRepo = Depends(get_workspace_repo)
) -> list[WorkspaceOut]:
    """The workspaces the signed-in person can open: every one for an admin."""
    return [WorkspaceOut.model_validate(w) for w in await workspaces.list() if user.can_open(w.id)]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceIn, workspaces: WorkspaceRepo = Depends(get_workspace_repo), _: CurrentUser = Depends(require_admin)
) -> WorkspaceOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the workspace a name")
    if await workspaces.name_taken(name):
        raise HTTPException(status_code=409, detail="A workspace with this name already exists")
    return WorkspaceOut.model_validate(await workspaces.create(name, body.description.strip()))


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: UUID,
    body: WorkspaceUpdate,
    workspaces: WorkspaceRepo = Depends(get_workspace_repo),
    _: CurrentUser = Depends(require_admin),
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
async def delete_workspace(
    workspace_id: UUID, workspaces: WorkspaceRepo = Depends(get_workspace_repo), _: CurrentUser = Depends(require_admin)
) -> Response:
    """Delete a workspace and everything it owns in our tables. Lead Intel is untouched."""
    workspace = await _get_or_404(workspaces, workspace_id)
    if len(await workspaces.list()) <= 1:
        raise HTTPException(status_code=409, detail="It's the only workspace. Create another one first.")
    await workspaces.delete(workspace)
    forget_sign_ins()
    return Response(status_code=204)


@router.put("/{workspace_id}/members", status_code=204, response_class=Response)
async def set_members(
    workspace_id: UUID,
    body: WorkspaceMembersIn,
    workspaces: WorkspaceRepo = Depends(get_workspace_repo),
    users: UserRepo = Depends(get_user_repo),
    _: CurrentUser = Depends(require_admin),
) -> Response:
    """Exactly these people can open the workspace (admins open every workspace anyway)."""
    await _get_or_404(workspaces, workspace_id)
    await users.set_workspace_members(workspace_id, body.user_ids)
    forget_sign_ins()
    return Response(status_code=204)
