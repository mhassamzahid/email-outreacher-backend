"""Who signs in and what they can open.

Accounts and passwords live in Supabase Auth, shared with Lead Intel, with public sign-up off. An admin
adds someone here with their role, workspaces and a first password: that makes their sign-in too
(services/supabase_admin.py), unless the email already has one, which they keep using. Only people added
here get in (dependencies.get_current_user); a Supabase account on its own isn't enough. Here: each
person's role and workspaces, in `eo_users` and `eo_workspace_members`.

Removing someone here only removes them from this app. Their Supabase account, which Lead Intel
shares, is left alone.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import UserRepo
from dependencies import forget_sign_ins, get_current_user, get_supabase_admin, get_user_repo, require_admin
from schemas import UserAdded, UserIn, UserOut, UserUpdate
from services.supabase_admin import SupabaseAdmin, SupabaseAdminError
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


@router.post("/users", response_model=UserAdded, status_code=201)
async def add_user(
    body: UserIn,
    _: CurrentUser = Depends(require_admin),
    users: UserRepo = Depends(get_user_repo),
    supabase: SupabaseAdmin | None = Depends(get_supabase_admin),
) -> UserAdded:
    """Add someone with their role and workspaces, and make their sign-in with the password given. An email
    that already has a sign-in (from Lead Intel) keeps it, and its own password."""
    email = body.email.strip().lower()
    if await users.by_email(email):
        raise HTTPException(status_code=409, detail="Someone with this email is already a user.")
    if supabase is None:
        raise HTTPException(status_code=503, detail="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to make sign-ins.")
    try:
        auth_id = await supabase.create_user(email, body.password)
    except SupabaseAdminError as exc:
        raise HTTPException(status_code=502 if (exc.status or 500) >= 500 else 422, detail=exc.message) from exc
    try:
        user = await users.create(body.name.strip() or email.split("@")[0], email, body.role, body.workspace_ids, auth_user_id=auth_id)
    except Exception:
        if auth_id:
            await supabase.delete_user(auth_id)  # no half-made person: the sign-in goes too
        raise
    out = _out(user, body.workspace_ids).model_dump()
    return UserAdded(**out, sign_in="created" if auth_id else "existing")


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
