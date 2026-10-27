from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dependencies import get_workspace_repo
from main import app


class FakeWorkspaceRepo:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    async def list(self):
        return list(self.rows)

    async def get(self, workspace_id):
        return next((w for w in self.rows if w.id == workspace_id), None)

    async def name_taken(self, name, except_id=None):
        return any(w.name.lower() == name.lower() and w.id != except_id for w in self.rows)

    async def create(self, name, description):
        w = SimpleNamespace(id=uuid4(), name=name, description=description, created_at=datetime.now(UTC))
        self.rows.append(w)
        return w

    async def update(self, workspace, values):
        for k, v in values.items():
            setattr(workspace, k, v)
        return workspace

    async def delete(self, workspace):
        self.rows.remove(workspace)


@pytest.fixture
def workspaces(client):
    repo = FakeWorkspaceRepo()
    app.dependency_overrides[get_workspace_repo] = lambda: repo
    return repo


def test_create_list_rename(client, workspaces) -> None:
    created = client.post("/api/v1/workspaces", json={"name": "  Partners ", "description": "Agency work"})
    assert created.status_code == 201, created.text
    ws = created.json()
    assert ws["name"] == "Partners" and ws["description"] == "Agency work" and "createdAt" in ws

    assert [w["name"] for w in client.get("/api/v1/workspaces").json()] == ["Partners"]

    renamed = client.patch(f"/api/v1/workspaces/{ws['id']}", json={"name": "Agency clients"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Agency clients"
    assert renamed.json()["description"] == "Agency work"


def test_names_are_unique_ignoring_case(client, workspaces) -> None:
    client.post("/api/v1/workspaces", json={"name": "AheadTech360"})
    other = client.post("/api/v1/workspaces", json={"name": "Other"}).json()
    assert client.post("/api/v1/workspaces", json={"name": "aheadtech360"}).status_code == 409
    assert client.patch(f"/api/v1/workspaces/{other['id']}", json={"name": "AHEADTECH360"}).status_code == 409
    # Keeping your own name isn't a clash.
    assert client.patch(f"/api/v1/workspaces/{other['id']}", json={"name": "other"}).status_code == 200


def test_blank_names_are_refused(client, workspaces) -> None:
    assert client.post("/api/v1/workspaces", json={"name": ""}).status_code == 422
    assert client.post("/api/v1/workspaces", json={"name": "   "}).status_code == 422


def test_delete_but_never_the_last_one(client, workspaces) -> None:
    a = client.post("/api/v1/workspaces", json={"name": "A"}).json()
    b = client.post("/api/v1/workspaces", json={"name": "B"}).json()
    assert client.delete(f"/api/v1/workspaces/{a['id']}").status_code == 204
    last = client.delete(f"/api/v1/workspaces/{b['id']}")
    assert last.status_code == 409 and "only workspace" in last.json()["detail"]
    assert client.delete(f"/api/v1/workspaces/{uuid4()}").status_code == 404
