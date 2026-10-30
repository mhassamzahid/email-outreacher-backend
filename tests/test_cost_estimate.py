"""The cost estimator: starting values, saving, validation and who may change it. No database."""

from uuid import uuid4

import pytest

from dependencies import get_current_user, get_settings_repo
from main import app
from schemas import CostEstimate
from services.auth import CurrentUser
from services.cost_estimate import DEFAULT_ESTIMATE
from tests.conftest import WORKSPACE_ID

WS = {"X-Workspace-Id": str(WORKSPACE_ID)}


class FakeSettings:
    def __init__(self) -> None:
        self.saved = None

    async def workspace_exists(self, workspace_id):
        return workspace_id == WORKSPACE_ID

    async def cost_estimate(self, workspace_id):
        return self.saved or DEFAULT_ESTIMATE

    async def save_cost_estimate(self, workspace_id, estimate):
        self.saved = estimate
        return estimate


@pytest.fixture
def store(client):
    fake = FakeSettings()
    app.dependency_overrides[get_settings_repo] = lambda: fake
    return fake


def test_the_starting_estimate_only_prices_what_has_a_source() -> None:
    estimate = CostEstimate(**DEFAULT_ESTIMATE)
    priced = {i.id: i.amount_usd for i in estimate.items if i.amount_usd}
    assert priced == {"cheapsequencer": 29, "cheapinboxes": 56, "claude-email": 0.0012, "claude-followups": 0.0014}
    assert all(i.note for i in estimate.items)  # every line says where its number comes from


def test_starting_values_until_one_is_saved(client, store) -> None:
    d = client.get("/api/v1/settings/cost-estimate", headers=WS).json()
    assert (d["leadsPerMonth"], d["emailsPerLead"], d["replyRatePct"]) == (2000, 3, 0)
    assert d["items"][0]["name"] == "CheapSequencer Starter"


def test_saving_your_own_items(client, store) -> None:
    body = {"leadsPerMonth": 500, "emailsPerLead": 2.6, "replyRatePct": 3,
            "items": [{"id": "x1", "name": "Clay", "category": "Data", "unit": "lead", "amountUsd": 0.05, "note": "", "enabled": True}]}  # fmt: skip
    saved = client.put("/api/v1/settings/cost-estimate", headers=WS, json=body)
    assert saved.status_code == 200, saved.text
    assert store.saved["items"][0]["amount_usd"] == 0.05 and store.saved["leads_per_month"] == 500
    assert client.get("/api/v1/settings/cost-estimate", headers=WS).json()["items"][0]["name"] == "Clay"


@pytest.mark.parametrize(
    "bad",
    [
        {"amountUsd": -1},
        {"unit": "year"},
        {"name": ""},
    ],
)
def test_nonsense_items_are_refused(client, store, bad) -> None:
    item = {"id": "a", "name": "A", "category": "Data", "unit": "lead", "amountUsd": 1, "note": "", "enabled": True} | bad
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={"leadsPerMonth": 1, "emailsPerLead": 1, "replyRatePct": 0, "items": [item]})
    assert r.status_code == 422 and store.saved is None


def test_two_items_with_one_id_are_refused(client, store) -> None:
    item = {"id": "a", "name": "A", "category": "Data", "unit": "lead", "amountUsd": 1, "note": "", "enabled": True}
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={"leadsPerMonth": 1, "emailsPerLead": 1, "replyRatePct": 0, "items": [item, item]})
    assert r.status_code == 422 and store.saved is None


def test_only_management_saves_but_everyone_in_the_workspace_reads(client, store) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(uuid4(), "s@x.com", "S", "sales", [WORKSPACE_ID])
    assert client.get("/api/v1/settings/cost-estimate", headers=WS).status_code == 200
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={**DEFAULT_ESTIMATE, "items": []})
    assert r.status_code == 403 and store.saved is None
