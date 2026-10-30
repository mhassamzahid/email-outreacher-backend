"""Who signs in and what they can open.

Accounts and passwords live in Supabase Auth (made and reset in the Supabase dashboard; there is no
sign-up). Here: each person's role and workspaces, in `eo_users` and `eo_workspace_members`.
Anyone with a Supabase account can sign in; on first sign-in they're added as Sales with no
workspaces, so they see nothing until an admin gives them some. An admin can also add someone by
email ahead of time, with their role and workspaces ready for their first sign-in.

Removing someone here only removes them from this app. Their Supabase account, which Lead Intel
shares, is left alone.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import UserRepo
from dependencies import forget_sign_ins, get_current_user, get_user_repo, require_admin
from schemas import UserIn, UserOut, UserUpdate
from services.auth import CurrentUser

router = APIRouter(tags=["users"])


def _out(user, workspace_ids: list[UUID]) -> UserOut:
    return UserOut.model_validate({
        "id": user.id, "name": user.name, "email": user.email, "role": user.role, "status": user.status,
        "workspace_ids": workspace_ids, "last_active_at": user.last_active_at, "created_at": user.created_at,
    })  # fmt: skip


async def _active_admins(users: UserRepo, except_id: UUID) -> int:
    return sum(1 for u in await users.list() if u.role == "admin" and u.status != "deactivated" and u.id != except_id)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_user), users: UserRepo = Depends(get_user_repo)) -> UserOut:
    row = await users.get(user.id)
    return _out(row, user.workspace_ids)


@router.get("/users", response_model=list[UserOut])
async def list_users(_: CurrentUser = Depends(require_admin), users: UserRepo = Depends(get_user_repo)) -> list[UserOut]:
    members = await users.members()
    return [_out(u, members.get(u.id, [])) for u in await users.list()]


@router.post("/users", response_model=UserOut, status_code=201)
async def add_user(body: UserIn, _: CurrentUser = Depends(require_admin), users: UserRepo = Depends(get_user_repo)) -> UserOut:
    """Add someone by email before they first sign in. Their Supabase account is made separately, in
    the Supabase dashboard."""
    email = body.email.strip().lower()
    if await users.by_email(email):
        raise HTTPException(status_code=409, detail="Someone with this email is already a user.")
    user = await users.create(body.name.strip() or email.split("@")[0], email, body.role, body.workspace_ids)
    return _out(user, body.workspace_ids)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: UUID, body: UserUpdate, admin: CurrentUser = Depends(require_admin), users: UserRepo = Depends(get_user_repo)
) -> UserOut:
    user = await users.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    values = body.model_dump(exclude_unset=True, exclude={"workspace_ids"})
    if "name" in values:
        values["name"] = (values["name"] or "").strip() or user.name
    losing_admin = user.role == "admin" and (
        values.get("role", "admin") != "admin" or values.get("status") == "deactivated"
    )
    if user.id == admin.id and (values.get("role", user.role), values.get("status", user.status)) != (user.role, user.status):
        raise HTTPException(status_code=409, detail="You can't change your own role or deactivate yourself. Ask another admin.")
    if losing_admin and await _active_admins(users, user.id) == 0:
        raise HTTPException(status_code=409, detail="This is the last active admin. Make someone else an admin first.")
    workspace_ids = body.workspace_ids if "workspace_ids" in body.model_fields_set else None
    user = await users.update(user, values, workspace_ids)
    forget_sign_ins()
    return _out(user, await users.workspace_ids(user.id))


@router.delete("/users/{user_id}", status_code=204, response_class=Response)
async def remove_user(user_id: UUID, admin: CurrentUser = Depends(require_admin), users: UserRepo = Depends(get_user_repo)) -> Response:
    """Remove someone from this app. Their Supabase account stays; if they sign in again they come
    back as Sales with no workspaces."""
    user = await users.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="You can't remove yourself.")
    if user.role == "admin" and await _active_admins(users, user.id) == 0:
        raise HTTPException(status_code=409, detail="This is the last active admin.")
    await users.delete(user)
    forget_sign_ins()
    return Response(status_code=204)
