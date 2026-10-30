"""The cost estimator: starting values, saving, validation and who may change it. No database."""

from uuid import uuid4

import pytest

from dependencies import get_current_user, get_settings_repo
from main import app
from schemas import CostEstimate
from services.auth import CurrentUser
from services.cost_estimate import DEFAULT_ESTIMATE, claude_price, upgrade
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
    priced = {i.id for i in estimate.items if i.price_usd}
    assert priced == {"email-generation", "follow-up", "verification", "maps-search", "maps-location", "cheapsequencer", "cheapinboxes"}
    assert all(i.note for i in estimate.items)  # every line says where its number comes from
    # Claude lines are priced from tokens and the model's price table.
    email = next(i for i in estimate.items if i.id == "email-generation")
    assert email.price_usd == claude_price(email.model, email.tokens_in, email.tokens_out) > 0


def test_an_estimate_saved_in_the_old_shape_still_opens() -> None:
    old = {"leads_per_month": 10, "emails_per_lead": 3, "reply_rate_pct": 0,
           "items": [{"id": "a", "name": "A", "category": "X", "unit": "lead", "amount_usd": 2, "note": "", "enabled": True}]}  # fmt: skip
    [item] = CostEstimate(**upgrade(old)).items
    assert (item.price_usd, item.quantity, item.basis, item.unit_label) == (2, 1, "lead", "per lead")


def test_starting_values_until_one_is_saved(client, store) -> None:
    d = client.get("/api/v1/settings/cost-estimate", headers=WS).json()
    assert (d["leadsPerMonth"], d["emailsPerLead"], d["replyRatePct"]) == (2000, 3, 0)
    assert d["items"][0]["name"] == "Email generation (hooks, email, score)"


def test_saving_your_own_items(client, store) -> None:
    body = {"leadsPerMonth": 500, "emailsPerLead": 2.6, "replyRatePct": 3,
            "items": [{"id": "x1", "name": "Clay", "category": "Data", "unitLabel": "per lookup", "priceUsd": 0.05, "quantity": 2,
                       "basis": "lead", "note": "", "enabled": True}]}  # fmt: skip
    saved = client.put("/api/v1/settings/cost-estimate", headers=WS, json=body)
    assert saved.status_code == 200, saved.text
    assert store.saved["items"][0]["price_usd"] == 0.05 and store.saved["items"][0]["quantity"] == 2
    assert store.saved["leads_per_month"] == 500
    assert client.get("/api/v1/settings/cost-estimate", headers=WS).json()["items"][0]["name"] == "Clay"


@pytest.mark.parametrize(
    "bad",
    [
        {"priceUsd": -1},
        {"basis": "year"},
        {"quantity": -2},
        {"name": ""},
    ],
)
def test_nonsense_items_are_refused(client, store, bad) -> None:
    item = {"id": "a", "name": "A", "category": "Data", "priceUsd": 1, "quantity": 1, "basis": "lead", "note": "", "enabled": True} | bad
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={"leadsPerMonth": 1, "emailsPerLead": 1, "replyRatePct": 0, "items": [item]})
    assert r.status_code == 422 and store.saved is None


def test_two_items_with_one_id_are_refused(client, store) -> None:
    item = {"id": "a", "name": "A", "category": "Data", "priceUsd": 1, "quantity": 1, "basis": "lead", "note": "", "enabled": True}
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={"leadsPerMonth": 1, "emailsPerLead": 1, "replyRatePct": 0, "items": [item, item]})
    assert r.status_code == 422 and store.saved is None


def test_only_management_saves_but_everyone_in_the_workspace_reads(client, store) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(uuid4(), "s@x.com", "S", "sales", [WORKSPACE_ID])
    assert client.get("/api/v1/settings/cost-estimate", headers=WS).status_code == 200
    r = client.put("/api/v1/settings/cost-estimate", headers=WS, json={**DEFAULT_ESTIMATE, "items": []})
    assert r.status_code == 403 and store.saved is None


def test_reference_gives_model_prices_and_measured_use(client) -> None:
    from dependencies import get_provider_call_repo
    from services.claude import PRICES

    class Calls:
        async def claude_per_use(self, workspace_id, since):
            return [{"action": "Write emails", "uses": 2, "tokens_in": 10182, "tokens_out": 2562, "cost_usd": 0.0023}]

    app.dependency_overrides[get_provider_call_repo] = lambda: Calls()
    d = client.get("/api/v1/settings/cost-estimate/reference", headers=WS).json()
    assert {p["model"] for p in d["claudePrices"]} == set(PRICES)
    assert d["measured"] == [{"action": "Write emails", "uses": 2, "tokensIn": 10182, "tokensOut": 2562, "costUsd": 0.0023}]
