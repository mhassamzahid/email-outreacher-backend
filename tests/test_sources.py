"""Verification and analysis sources: the list, Apify prices for the plan tier, and what an ICP may choose."""

import asyncio

import httpx
import pytest

from schemas import IcpProfileIn
from services import sources
from services.apify import ApifyClient
from services.sources import SOURCES, apify_prices, describe, event_price


def actor(events: dict) -> dict:
    return {"pricingInfos": [{"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": events}}]}


TIERED = {"post": {"eventTieredPricingUsd": {"FREE": {"tieredEventPriceUsd": 0.005}, "GOLD": {"tieredEventPriceUsd": 0.002}}},
          "actor-start": {"eventPriceUsd": 0.001}}  # fmt: skip


def test_the_price_follows_the_plan_tier() -> None:
    a = actor(TIERED)
    assert event_price(a, "post", "FREE") == 0.005
    assert event_price(a, "post", "GOLD") == 0.002
    assert event_price(a, "post", "SILVER") == 0.005  # a tier the actor doesn't list: its Free price
    assert event_price(a, "actor-start", "GOLD") == 0.001  # a flat price
    assert event_price(a, "nope", "FREE") is None and event_price(a, None, "FREE") is None


def test_every_source_has_an_id_kind_and_apify_sources_name_their_actor() -> None:
    assert len({(s.kind, s.id) for s in SOURCES}) == len(SOURCES)
    assert all(s.actor and s.event for s in SOURCES if s.provider == "apify")
    live = {(s.kind, s.id) for s in SOURCES if s.status == "live"}
    assert live == {("verification", "website"), ("verification", "jina"), ("verification", "instagram"), ("verification", "score"),
                    ("analysis", "linkedin"), ("analysis", "instagram")}  # fmt: skip


@pytest.fixture(autouse=True)
def fresh_cache():
    sources._cache.clear()
    yield
    sources._cache.clear()


def fake_apify(tier: str, missing: str | None = None) -> ApifyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"data": {"plan": {"tier": tier}}})
        if missing and missing in request.url.path:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json={"data": actor(
            {e: {"eventTieredPricingUsd": {"FREE": {"tieredEventPriceUsd": 0.01}, "GOLD": {"tieredEventPriceUsd": 0.004}}}
             for e in ("post", "result", "profile", "review-scraped", "apify-default-dataset-item")} | {"actor-start": {"eventPriceUsd": 0.001}}
        )})  # fmt: skip

    return ApifyClient("k", transport=httpx.MockTransport(handler))


def test_prices_come_from_apify_for_the_accounts_tier() -> None:
    tier, prices = asyncio.run(apify_prices(fake_apify("GOLD")))
    assert tier == "GOLD"
    assert prices["analysis:facebook"] == (0.004, 0.001)  # per post, plus its per-run charge
    assert prices["analysis:x"] == (0.004, None)  # no per-run charge
    priced = {s["id"] for s in describe(prices) if s["unit_price"]}
    assert "website" not in priced and "score" not in priced  # not Apify


def test_one_missing_actor_never_hides_the_others() -> None:
    _, prices = asyncio.run(apify_prices(fake_apify("FREE", missing="tweet-scraper")))
    assert "analysis:x" not in prices and prices["analysis:linkedin"][0] == 0.01


def test_an_upgrade_shows_once_the_hourly_tier_check_comes_round(monkeypatch) -> None:
    asyncio.run(apify_prices(fake_apify("FREE")))
    tier, prices = asyncio.run(apify_prices(fake_apify("GOLD")))
    assert tier == "FREE"  # within the hour: still the cached tier
    clock = sources.time.monotonic()
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock + sources.TIER_CACHE_SECONDS + 1)
    tier, prices = asyncio.run(apify_prices(fake_apify("GOLD")))
    assert tier == "GOLD" and prices["analysis:facebook"][0] == 0.004


def test_an_icp_only_saves_sources_coldbrew_knows() -> None:
    p = IcpProfileIn(verification_sources=["website", "facebook_page", "website"], enabled_sources=["linkedin", "website", "x"])
    assert p.verification_sources == ["website", "facebook_page"]
    assert p.enabled_sources == ["linkedin", "x"]  # "website" never fetched posts
    with pytest.raises(ValueError, match="Unknown verification sources: myspace"):
        IcpProfileIn(verification_sources=["myspace"])


def test_without_apify_the_sources_still_list(client) -> None:
    from dependencies import get_optional_apify
    from main import app

    app.dependency_overrides[get_optional_apify] = lambda: None
    d = client.get("/api/v1/sources").json()
    assert d["tier"] is None and "APIFY_TOKEN" in d["note"]
    assert {s["id"] for s in d["sources"] if s["kind"] == "analysis"} >= {"linkedin", "facebook", "x", "tiktok", "youtube"}
