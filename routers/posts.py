"""Leads' recent LinkedIn and Instagram posts, fetched through Apify.

LinkedIn: the lead's own profile posts, when the lead has a LinkedIn URL.
Instagram: the lead's Instagram, or else the company's account that Lead Intel already found.
One Apify run per source covers all the leads asked for, and posts are matched back to each lead.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from crud import PostRepo, ProviderCallRepo
from dependencies import get_apify, get_lead_intel_repo, get_post_repo, get_provider_call_repo, require_workspace_id
from lead_intel import LeadIntelRepo
from schemas import LeadPostsResult, PostFetchIn, PostFetchResult, PostOut
from services.apify import (
    INSTAGRAM_ACTOR,
    LINKEDIN_POSTS_ACTOR,
    ApifyClient,
    ApifyError,
    FetchedPost,
    RunResult,
    instagram_handle,
    linkedin_slug,
)

router = APIRouter(prefix="/posts", tags=["posts"])

# Statuses a fetch may move a lead out of. Leads already drafted, approved or sent keep theirs.
REFRESHABLE = {"imported", "enriching", "enriched", "no_content", "failed"}


@router.get("", response_model=list[PostOut])
async def list_posts(
    lead_id: UUID | None = Query(default=None, alias="leadId"),
    workspace_id: UUID = Depends(require_workspace_id),
    posts: PostRepo = Depends(get_post_repo),
) -> list[PostOut]:
    return [PostOut.model_validate(p) for p in await posts.list(workspace_id, lead_id)]


def _group(found: list[FetchedPost], keys: dict[UUID, str]) -> dict[UUID, list[FetchedPost]]:
    """Match posts to leads by profile slug or Instagram username. With a single input, every post is its."""
    by_lead: dict[UUID, list[FetchedPost]] = defaultdict(list)
    distinct = set(keys.values())
    for post in found:
        for lead_id, key in keys.items():
            if len(distinct) == 1 or post.owner_key == key:
                by_lead[lead_id].append(post)
    return by_lead


@router.post("/fetch", response_model=PostFetchResult)
async def fetch_posts(
    body: PostFetchIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    posts: PostRepo = Depends(get_post_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apify: ApifyClient = Depends(get_apify),
) -> PostFetchResult:
    """Fetch recent posts for these leads. Paid per post (about $0.002 each on Apify)."""
    leads = await posts.leads(workspace_id, list(dict.fromkeys(body.lead_ids)))
    since = datetime.now(UTC) - timedelta(days=body.max_age_days)

    # Company Instagram accounts from Lead Intel (read-only), for leads without their own.
    company_ids = list({lead.lead_intel_business_id for lead in leads if lead.lead_intel_business_id and not lead.instagram_url})
    companies, _ = await lead_intel.list_companies(None, None, max(len(company_ids), 1), 0, only_ids=company_ids)
    company_ig = {c["id"]: c["fields"].get("instagram_url") for c in companies}

    linkedin = {lead.id: slug for lead in leads if (slug := linkedin_slug(lead.linkedin_url))}
    linkedin_urls = {lead.id: lead.linkedin_url for lead in leads if lead.id in linkedin}
    instagram: dict[UUID, str] = {}
    instagram_urls: dict[str, str] = {}
    for lead in leads:
        url = lead.instagram_url or company_ig.get(lead.lead_intel_business_id)
        if handle := instagram_handle(url):
            instagram[lead.id] = handle
            instagram_urls[handle] = f"https://www.instagram.com/{handle}/"

    found: dict[str, dict[UUID, list[FetchedPost]]] = {"linkedin": {}, "instagram": {}}
    cost = 0.0
    error: str | None = None

    async def run(source: str, actor: str, call) -> None:
        nonlocal cost, error
        try:
            fetched, result = await call()
        except ApifyError as exc:
            await calls.record(workspace_id, "apify", actor, "failed", action="Fetch posts")
            error = exc.message
            return
        cost += _cost(result)
        await calls.record(
            workspace_id, "apify", actor, "succeeded",
            key=f"apify:run:{result.run_id}" if result.run_id else None, cost_usd=result.cost_usd, action="Fetch posts",
        )  # fmt: skip
        found[source] = _group(fetched, linkedin if source == "linkedin" else instagram)

    if linkedin:
        urls = list(dict.fromkeys(linkedin_urls.values()))
        await run("linkedin", LINKEDIN_POSTS_ACTOR, lambda: apify.linkedin_posts(urls, body.max_posts, since))
    if instagram and not error:
        urls = list(instagram_urls.values())
        await run("instagram", INSTAGRAM_ACTOR, lambda: apify.instagram_posts(urls, body.max_posts, since))

    results = []
    for lead in leads:
        li = found["linkedin"].get(lead.id, [])
        ig = found["instagram"].get(lead.id, [])
        new = await posts.save(workspace_id, lead.id, li + ig) if li or ig else 0
        if not error and lead.status in REFRESHABLE:
            await posts.set_status(lead, "enriched" if li or ig else "no_content")
        results.append(
            LeadPostsResult(
                lead_id=lead.id,
                linkedin=len(li) if lead.id in linkedin else None,
                instagram=len(ig) if lead.id in instagram else None,
                new=new,
            )
        )
    return PostFetchResult(results=results, cost_usd=round(cost, 4), error=error)


def _cost(result: RunResult) -> float:
    return result.cost_usd or 0.0
