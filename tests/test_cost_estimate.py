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
    unpriced = {i.id for i in estimate.items if not i.price_usd}
    # Apollo, Apify people search, Jina, hosting: no source Coldbrew can read, so COST_PRICES fills them in.
    assert unpriced == {"prompt-improvement", "apollo-companies", "apollo-people", "apify-linkedin-people", "apollo-reveal",
                        "website-pages", "railway", "vercel", "supabase"}  # fmt: skip
    assert all(i.note for i in estimate.items)  # every line says where its number comes from
    # Claude lines are priced from tokens and the model's price table.
    email = next(i for i in estimate.items if i.id == "email-generation")
    assert email.price_usd == claude_price(email.model, email.tokens_in, email.tokens_out) > 0


def test_apify_prices_beat_the_starting_ones_and_cost_prices_beat_both() -> None:
    from services.cost_estimate import refresh

    apify = {"analysis:facebook": (0.0008, 0.001), "verification:google_reviews": (0.00015, 5e-05)}
    out = refresh(DEFAULT_ESTIMATE, "claude-haiku-5-5", {"google-reviews": 0.01}, apify)
    price = {i["id"]: i["price_usd"] for i in out["items"]}
    assert price["facebook-posts"] == 0.0008  # Apify's price for the plan
    assert price["google-reviews"] == 0.01  # COST_PRICES wins
    assert price["tiktok-videos"] == 0.0037  # Apify said nothing: the starting price
    planned = {i["id"] for i in out["items"] if i["planned"]}
    assert "facebook-posts" in planned and "linkedin-posts" not in planned and "instagram-profile" not in planned


def test_new_lines_are_added_to_an_older_saved_estimate() -> None:
    from services.cost_estimate import refresh

    old = {**DEFAULT_ESTIMATE, "items": [i for i in DEFAULT_ESTIMATE["items"] if i["id"] != "x-posts"]}
    ids = [i["id"] for i in refresh(old, "claude-haiku-5-5", {})["items"]]
    assert ids.count("x-posts") == 1


def test_verification_counted_a_month_becomes_companies_per_month() -> None:
    old = {"leads_per_month": 10, "emails_per_lead": 3, "reply_rate_pct": 0, "pages_per_company": 4, "items": [
        {"id": "verification", "name": "x", "category": "Verification", "unit_label": "per company", "price_usd": 0.0007,
         "quantity": 300, "basis": "month", "model": "claude-haiku-5-5", "tokens_in": 1, "tokens_out": 1, "action": None,
         "note": "", "enabled": True},
        {"id": "website-pages", "name": "x", "category": "Verification", "unit_label": "per page", "price_usd": 0,
         "quantity": 1200, "basis": "month", "model": None, "tokens_in": None, "tokens_out": None, "action": None,
         "note": "", "enabled": True},
    ]}  # fmt: skip
    up = upgrade(old)
    assert up["companies_per_month"] == 300 and "pages_per_company" not in up
    by_id = {i["id"]: i for i in up["items"]}
    assert (by_id["verification"]["basis"], by_id["verification"]["quantity"]) == ("company", 1)
    assert (by_id["website-pages"]["basis"], by_id["website-pages"]["quantity"]) == ("company", 4)


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


def test_prices_are_read_only_and_come_from_the_server(client, store, monkeypatch) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "cost_prices", '{"apollo-reveal": 0.02, "railway": 20, "bad": -1, "worse": "x"}')
    monkeypatch.setattr(get_settings(), "claude_scoring_model", "claude-sonnet-5-5")
    body = client.get("/api/v1/settings/cost-estimate", headers=WS).json()
    for item in body["items"]:
        if item["id"] in ("cheapsequencer", "apollo-reveal"):
            item["priceUsd"] = 999  # the browser trying to change a price
    body["companiesPerMonth"] = 400
    saved = client.put("/api/v1/settings/cost-estimate", headers=WS, json=body).json()
    price = {i["id"]: i["priceUsd"] for i in saved["items"]}
    assert (price["cheapsequencer"], price["apollo-reveal"], price["railway"]) == (29, 0.02, 20)
    assert saved["companiesPerMonth"] == 400 and store.saved["companies_per_month"] == 400
    verification = next(i for i in saved["items"] if i["id"] == "verification")
    assert verification["model"] == "claude-sonnet-5-5"
    assert verification["priceUsd"] == claude_price("claude-sonnet-5-5", verification["tokensIn"], verification["tokensOut"])


def test_cost_prices_ignores_what_isnt_a_price(monkeypatch) -> None:
    from config import get_settings

    for raw, expected in (("", {}), ("not json", {}), ("[1]", {}), ('{"a": 1, "b": -2, "c": true, "d": "3"}', {"a": 1.0})):
        monkeypatch.setattr(get_settings(), "cost_prices", raw)
        assert get_settings().cost_price_overrides == expected
