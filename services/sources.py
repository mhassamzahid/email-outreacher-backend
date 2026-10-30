"""Every source Coldbrew can use, and what each one costs.

Two kinds:
  verification  what research reads to decide whether a business is worth contacting
  analysis      posts read to find something specific to open the email with

`live` sources run today. `planned` ones can be chosen on an ICP and show their cost, but Coldbrew doesn't
run their actor yet: each needs one small live run to confirm its output before it's wired in.

Apify prices come from Apify itself (the actor's published price for the account's plan tier), so they
stay right when Apify or the plan changes. See `apify_prices`.
"""

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from services.apify import ApifyClient, ApifyError

Kind = Literal["verification", "analysis"]
Level = Literal["company", "lead"]


@dataclass(frozen=True)
class Source:
    id: str
    kind: Kind
    level: Level
    label: str
    hint: str
    provider: Literal["coldbrew", "jina", "claude", "apify"]
    unit: str = ""
    actor: str | None = None  # Apify actor, "username/name"
    event: str | None = None  # the actor's pay-per-event charge for one unit
    start_event: str | None = None  # its charge per run, if any
    status: Literal["live", "planned"] = "live"


SOURCES: tuple[Source, ...] = (
    # ---- Verification: is this business worth contacting? ----
    Source("website", "verification", "company", "Website", "Read by Coldbrew's own reader. Rules on website words need it.", "coldbrew", "per page"),
    Source("jina", "verification", "company", "Jina fallback", "Reads a site when it blocks Coldbrew's reader.", "jina", "per page"),
    Source("instagram", "verification", "company", "Instagram profile", "Followers, bio and recent posts.", "apify", "per profile",
           "apify/instagram-profile-scraper", "profile"),
    Source("facebook_page", "verification", "company", "Facebook page", "Likes, followers, category and about.", "apify", "per page",
           "apify/facebook-pages-scraper", "apify-default-dataset-item", status="planned"),
    Source("linkedin_company", "verification", "company", "LinkedIn company page", "Size, industry, description and followers.", "apify",
           "per company", "harvestapi/linkedin-company", "apify-default-dataset-item", "apify-actor-start", status="planned"),
    Source("google_reviews", "verification", "company", "Google reviews", "Recent reviews, for companies found on Google Maps.", "apify",
           "per review", "compass/Google-Maps-Reviews-Scraper", "review-scraped", "apify-actor-start", status="planned"),
    Source("tiktok_profile", "verification", "company", "TikTok profile", "Followers and recent videos.", "apify", "per result",
           "clockworks/tiktok-profile-scraper", "result", status="planned"),
    Source("youtube_channel", "verification", "company", "YouTube channel", "Subscribers and recent videos.", "apify", "per video",
           "streamers/youtube-channel-scraper", "result", status="planned"),
    Source("score", "verification", "company", "Claude's fit score", "Scores the fit from everything read. Off: the ICP's rules alone decide.",
           "claude", "per company"),
    # ---- Analysis: posts to open the email with ----
    Source("linkedin", "analysis", "lead", "LinkedIn posts", "The lead's own LinkedIn posts.", "apify", "per post",
           "harvestapi/linkedin-profile-posts", "post", "apify-actor-start"),
    Source("instagram", "analysis", "lead", "Instagram posts", "The lead's (or their company's) Instagram posts.", "apify", "per post",
           "apify/instagram-scraper", "result"),
    Source("facebook", "analysis", "lead", "Facebook posts", "The lead's or their company's Facebook posts.", "apify", "per post",
           "apify/facebook-posts-scraper", "post", "actor-start", status="planned"),
    Source("linkedin_company_posts", "analysis", "company", "Company LinkedIn posts", "Posts from the company's LinkedIn page.", "apify",
           "per post", "harvestapi/linkedin-company-posts", "post", "apify-actor-start", status="planned"),
    Source("x", "analysis", "company", "X posts", "The company's posts on X, from the link on its website.", "apify", "per post",
           "apidojo/tweet-scraper", "apify-default-dataset-item", status="planned"),
    Source("tiktok", "analysis", "company", "TikTok videos", "The company's TikTok videos, from the link on its website.", "apify",
           "per video", "clockworks/tiktok-scraper", "result", "actor-start", status="planned"),
    Source("youtube", "analysis", "company", "YouTube videos", "The company's YouTube videos, from the link on its website.", "apify",
           "per video", "streamers/youtube-scraper", "result", status="planned"),
)  # fmt: skip

VERIFICATION_IDS = tuple(s.id for s in SOURCES if s.kind == "verification")
ANALYSIS_IDS = tuple(s.id for s in SOURCES if s.kind == "analysis")

# ---- Apify prices, from Apify ----

CACHE_SECONDS = 12 * 3600
# The plan tier is checked hourly, so an upgrade shows up in the prices within the hour.
TIER_CACHE_SECONDS = 3600
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, seconds: int = CACHE_SECONDS) -> Any:
    hit = _cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < seconds else None


def event_price(actor: dict[str, Any], event: str | None, tier: str) -> float | None:
    """One pay-per-event charge for the plan tier, from an actor as Apify describes it."""
    if not event:
        return None
    infos = actor.get("pricingInfos") or []
    current = infos[-1] if infos else {}
    charge = ((current.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}).get(event) or {}
    if charge.get("eventPriceUsd") is not None:
        return float(charge["eventPriceUsd"])
    tiers = charge.get("eventTieredPricingUsd") or {}
    chosen = tiers.get(tier) or tiers.get("FREE")
    return float(chosen["tieredEventPriceUsd"]) if chosen and chosen.get("tieredEventPriceUsd") is not None else None


async def apify_prices(apify: ApifyClient) -> tuple[str, dict[str, tuple[float | None, float | None]]]:
    """The account's plan tier, and each Apify source's (price per unit, price per run) for it."""
    tier = _cached("tier", TIER_CACHE_SECONDS)
    if tier is None:
        tier = ((await apify.get("/users/me")).get("data", {}).get("plan") or {}).get("tier") or "FREE"
        _cache["tier"] = (time.monotonic(), tier)

    async def actor(name: str) -> dict[str, Any] | None:
        hit = _cached(name)
        if hit is None:
            try:
                hit = (await apify.get(f"/acts/{name.replace('/', '~')}")).get("data") or {}
            except ApifyError:
                return None  # one missing actor never hides the others
            _cache[name] = (time.monotonic(), hit)
        return hit

    # All at once: one after another, a cold cache took about ten seconds.
    names = sorted({s.actor for s in SOURCES if s.actor})
    actors = dict(zip(names, await asyncio.gather(*(actor(n) for n in names)), strict=True))
    prices: dict[str, tuple[float | None, float | None]] = {}
    for source in SOURCES:
        found = actors.get(source.actor) if source.actor else None
        if found is not None:
            prices[f"{source.kind}:{source.id}"] = (event_price(found, source.event, tier), event_price(found, source.start_event, tier))
    return tier, prices


def describe(prices: dict[str, tuple[float | None, float | None]] | None) -> list[dict[str, Any]]:
    out = []
    for source in SOURCES:
        unit_price, run_price = (prices or {}).get(f"{source.kind}:{source.id}", (None, None))
        out.append({**asdict(source), "unit_price": unit_price, "run_price": run_price})
    return out
