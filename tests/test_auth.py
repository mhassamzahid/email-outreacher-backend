"""Sign-in and access: tokens signed with a key made here (no Supabase), and fake user storage.
No network, no cost."""

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import services.auth as auth
from config import get_settings
from dependencies import get_current_user, get_user_repo, get_workspace_repo
from main import app
from services.auth import AuthError, CurrentUser, verify
from tests.conftest import WORKSPACE_ID

ISSUER = "https://example.supabase.co/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def token(key=KEY, **claims) -> str:
    now = int(time.time())
    body = {"sub": str(uuid4()), "email": "Ava@Example.com", "aud": "authenticated", "iss": ISSUER, "iat": now, "exp": now + 3600}
    return jwt.encode(body | claims, key, algorithm="ES256", headers={"kid": "k1"})


@pytest.fixture
def jwks(monkeypatch):
    """Supabase's key endpoint, answered with this test's public key."""
    monkeypatch.setattr(auth, "_jwks", lambda url: SimpleNamespace(get_signing_key_from_jwt=lambda t: SimpleNamespace(key=KEY.public_key())))


def run(coro):
    return asyncio.run(coro)


def test_small_clock_differences_are_allowed(jwks) -> None:
    # Issued "2 seconds from now" by a clock slightly ahead of ours: still accepted.
    assert run(verify(token(iat=int(time.time()) + 2), "jwks", ISSUER)).email == "ava@example.com"


def test_valid_token(jwks) -> None:
    sub = uuid4()
    claims = run(verify(token(sub=str(sub)), "jwks", ISSUER))
    assert claims.auth_user_id == sub and claims.email == "ava@example.com"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (lambda: token(exp=int(time.time()) - 120), "expired"),
        (lambda: token(OTHER_KEY), "isn't valid"),
        (lambda: token(aud="anon"), "isn't valid"),
        (lambda: token(iss="https://other.supabase.co/auth/v1"), "isn't valid"),
        (lambda: token(email=""), "no account email"),
        (lambda: "not-a-token", "isn't valid"),
    ],
)
def test_bad_tokens(jwks, bad, message) -> None:
    with pytest.raises(AuthError, match=message):
        run(verify(bad(), "jwks", ISSUER))


# ---------- Users and access through the API ----------


class FakeUsers:
    def __init__(self) -> None:
        self.rows: dict[UUID, SimpleNamespace] = {}
        self.ws: dict[UUID, list[UUID]] = {}

    def add(self, email, role="sales", status="active", auth_user_id=None, workspaces=()):
        row = SimpleNamespace(id=uuid4(), auth_user_id=auth_user_id, email=email, name=email.split("@")[0], role=role,
                              status=status, last_active_at=None, created_at=datetime.now(UTC))  # fmt: skip
        self.rows[row.id] = row
        self.ws[row.id] = list(workspaces)
        return row

    async def for_sign_in(self, auth_user_id, email):
        row = next((r for r in self.rows.values() if r.auth_user_id == auth_user_id), None)
        if row is None:
            row = next((r for r in self.rows.values() if r.email == email), None)
            if row is not None:
                row.auth_user_id = auth_user_id
            else:
                row = self.add(email, auth_user_id=auth_user_id)
        if row.status == "invited":
            row.status = "active"
        return row

    async def workspace_ids(self, user_id):
        return self.ws.get(user_id, [])

    async def members(self):
        return self.ws

    async def list(self):
        return list(self.rows.values())

    async def get(self, user_id):
        return self.rows.get(user_id)

    async def by_email(self, email):
        return next((r for r in self.rows.values() if r.email == email), None)

    async def create(self, name, email, role, workspace_ids):
        row = self.add(email, role, "invited", workspaces=workspace_ids)
        row.name = name
        return row

    async def update(self, user, values, workspace_ids):
        for k, v in values.items():
            setattr(user, k, v)
        if workspace_ids is not None:
            self.ws[user.id] = workspace_ids
        return user

    async def delete(self, user):
        self.rows.pop(user.id)


class FakeWorkspaces:
    def __init__(self) -> None:
        self.rows = [SimpleNamespace(id=WORKSPACE_ID, name="AheadTech360", description="", created_at=datetime.now(UTC)),
                     SimpleNamespace(id=uuid4(), name="Other", description="", created_at=datetime.now(UTC))]  # fmt: skip

    async def list(self):
        return self.rows


@pytest.fixture
def signed(jwks, monkeypatch):
    """A client that signs in for real: the token is checked, the user looked up in FakeUsers."""
    monkeypatch.setattr(get_settings(), "supabase_url", "https://example.supabase.co")
    users, workspaces = FakeUsers(), FakeWorkspaces()
    app.dependency_overrides[get_user_repo] = lambda: users
    app.dependency_overrides[get_workspace_repo] = lambda: workspaces
    app.dependency_overrides.pop(get_current_user, None)
    yield SimpleNamespace(client=TestClient(app), users=users, workspaces=workspaces)
    app.dependency_overrides.clear()


def as_(user, **headers):
    return {"Authorization": f"Bearer {token(sub=str(user.auth_user_id), email=user.email)}", **headers}


def test_requests_need_a_session(signed) -> None:
    c = signed.client
    assert c.get("/api/v1/health").status_code == 200  # the one open route
    assert c.get("/api/v1/workspaces").json()["detail"] == "Sign in to continue."
    assert c.get("/api/v1/workspaces", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert c.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token(OTHER_KEY)}"}).status_code == 401


def test_first_sign_in_is_sales_with_nothing(signed) -> None:
    newcomer = SimpleNamespace(auth_user_id=uuid4(), email="new@example.com")
    me = signed.client.get("/api/v1/me", headers=as_(newcomer)).json()
    assert (me["email"], me["role"], me["workspaceIds"]) == ("new@example.com", "sales", [])
    assert signed.client.get("/api/v1/workspaces", headers=as_(newcomer)).json() == []
    r = signed.client.get("/api/v1/leads", headers=as_(newcomer, **{"X-Workspace-Id": str(WORKSPACE_ID)}))
    assert r.status_code == 403 and "access to this workspace" in r.json()["detail"]
    assert signed.client.get("/api/v1/users", headers=as_(newcomer)).status_code == 403


def test_added_by_email_links_on_first_sign_in(signed) -> None:
    row = signed.users.add("research@aheadtech360.com", role="admin", status="invited")
    auth_id = uuid4()
    me = signed.client.get("/api/v1/me", headers=as_(SimpleNamespace(auth_user_id=auth_id, email=row.email))).json()
    assert me["id"] == str(row.id) and me["role"] == "admin" and me["status"] == "active" and row.auth_user_id == auth_id
    # Admins open every workspace.
    assert len(signed.client.get("/api/v1/workspaces", headers=as_(row)).json()) == 2


def test_members_see_only_their_workspaces_and_deactivated_are_refused(signed) -> None:
    member = signed.users.add("sam@example.com", auth_user_id=uuid4(), workspaces=[WORKSPACE_ID])
    assert [w["name"] for w in signed.client.get("/api/v1/workspaces", headers=as_(member)).json()] == ["AheadTech360"]
    member.status = "deactivated"
    r = signed.client.get("/api/v1/workspaces", headers=as_(member))
    assert r.status_code == 403 and "deactivated" in r.json()["detail"]


def test_admins_manage_users_with_guards(signed) -> None:
    admin = signed.users.add("boss@example.com", role="admin", auth_user_id=uuid4())
    c, h = signed.client, as_(admin)
    added = c.post("/api/v1/users", headers=h, json={"name": "Ava", "email": "Ava@Example.com", "role": "management", "workspaceIds": [str(WORKSPACE_ID)]})
    assert added.status_code == 201 and added.json()["email"] == "ava@example.com" and added.json()["status"] == "invited"
    assert c.post("/api/v1/users", headers=h, json={"email": "ava@example.com"}).status_code == 409
    assert c.post("/api/v1/users", headers=h, json={"email": "not an email"}).status_code == 422
    ava = added.json()["id"]
    moved = c.patch(f"/api/v1/users/{ava}", headers=h, json={"role": "sales", "workspaceIds": []}).json()
    assert (moved["role"], moved["workspaceIds"]) == ("sales", [])
    # You can't demote or deactivate yourself, and the last admin can't be lost.
    assert c.patch(f"/api/v1/users/{admin.id}", headers=h, json={"role": "sales"}).status_code == 409
    assert c.patch(f"/api/v1/users/{admin.id}", headers=h, json={"name": "The Boss"}).json()["name"] == "The Boss"
    assert c.delete(f"/api/v1/users/{admin.id}", headers=h).status_code == 409
    assert c.delete(f"/api/v1/users/{ava}", headers=h).status_code == 204
    assert [u["email"] for u in c.get("/api/v1/users", headers=h).json()] == ["boss@example.com"]


def test_only_admins_change_workspaces_and_management_changes_icps(signed) -> None:
    sales = signed.users.add("s@example.com", auth_user_id=uuid4(), workspaces=[WORKSPACE_ID])
    assert signed.client.post("/api/v1/workspaces", headers=as_(sales), json={"name": "Mine"}).status_code == 403
    assert signed.client.put(f"/api/v1/workspaces/{WORKSPACE_ID}/members", headers=as_(sales), json={"userIds": []}).status_code == 403
    icp = {"name": "X", "industry": "Y"}
    assert signed.client.post("/api/v1/icps", headers=as_(sales), json=icp).json()["detail"] == "Only Management and admins can change ICPs."


def test_current_user_can_open() -> None:
    sales = CurrentUser(uuid4(), "s@x.com", "S", "sales", [WORKSPACE_ID])
    assert sales.can_open(WORKSPACE_ID) and not sales.can_open(uuid4())
    assert CurrentUser(uuid4(), "a@x.com", "A", "admin").can_open(uuid4())
