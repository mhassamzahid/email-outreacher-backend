"""Apify actors: LinkedIn company employees, LinkedIn profile posts, Instagram posts.

Each call starts a run, waits for it (up to RUN_TIMEOUT), then reads its dataset. Apify bills per
result; the run's actual cost (`usageTotalUsd`) is returned so it can be logged as spend.

Actors (all public, no cookies needed):
  harvestapi~linkedin-company-employees  ~$4 per 1,000 profiles in Short mode
  harvestapi~linkedin-profile-posts      ~$2 per 1,000 posts
  apify~instagram-scraper                ~$1.50–2.70 per 1,000 results, by plan
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

BASE_URL = "https://api.apify.com/v2"
EMPLOYEES_ACTOR = "harvestapi~linkedin-company-employees"
LINKEDIN_POSTS_ACTOR = "harvestapi~linkedin-profile-posts"
INSTAGRAM_ACTOR = "apify~instagram-scraper"

RUN_TIMEOUT = 300  # seconds to wait for a run before giving up on it
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(Exception):
    """Apify refused or a run didn't finish. `message` is written for the person using the app."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class RunResult:
    items: list[dict[str, Any]]
    cost_usd: float | None
    run_id: str | None


@dataclass
class Employee:
    external_id: str
    first_name: str
    last_name: str
    title: str
    linkedin_url: str | None
    company_name: str | None
    raw: dict[str, Any]


@dataclass
class FetchedPost:
    source: str  # linkedin | instagram
    url: str
    external_id: str | None
    posted_at: datetime | None
    text: str
    media_type: str  # text | image | video | carousel | article
    is_repost: bool
    # Which input this post belongs to: a LinkedIn profile slug, or an Instagram username.
    owner_key: str | None
    raw: dict[str, Any]


def linkedin_slug(url: str | None) -> str | None:
    """'https://www.linkedin.com/in/Ava-Morgan/' -> 'ava-morgan'."""
    if not url:
        return None
    match = re.search(r"linkedin\.com/(?:in|company)/([^/?#]+)", url, re.I)
    return match.group(1).lower() if match else None


def instagram_handle(url: str | None) -> str | None:
    """'https://instagram.com/samedaycustom/' -> 'samedaycustom'. Post and reel URLs give None."""
    if not url:
        return None
    path = [p for p in urlsplit(url if "://" in url else f"https://{url}").path.split("/") if p]
    if not path or path[0] in {"p", "reel", "reels", "explore", "stories"}:
        return None
    return path[0].lstrip("@").lower()


def _message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        detail = error.get("message") or error.get("type")
    except ValueError:
        detail = response.text[:200]
    if response.status_code == 401:
        return "Apify rejected the token. Check APIFY_TOKEN."
    if response.status_code == 402:
        return f"Apify says the account is out of credit or over its plan limit. {detail or ''}".strip()
    if response.status_code == 429:
        return "Apify's rate limit was reached. Try again in a minute."
    return f"Apify answered {response.status_code}: {detail or 'no details'}"


def _date(value: Any) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("date") or value.get("timestamp")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class ApifyClient:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None, poll_seconds: float = 5) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=90, transport=transport
        )
        self._poll = poll_seconds

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApifyError(f"Couldn't reach Apify: {exc}") from exc
        if response.status_code >= 400:
            raise ApifyError(_message(response), response.status_code)
        return response.json()

    async def run(self, actor: str, actor_input: dict[str, Any], max_items: int) -> RunResult:
        """Start a run, wait for it, and read up to `max_items` results."""
        run = (await self._request("POST", f"/acts/{actor}/runs", json=actor_input, params={"waitForFinish": 60}))["data"]
        waited = 60
        while run.get("status") not in TERMINAL and waited < RUN_TIMEOUT:
            await asyncio.sleep(self._poll)
            run = (await self._request("GET", f"/actor-runs/{run['id']}", params={"waitForFinish": 60}))["data"]
            waited += 60
        status = run.get("status")
        if status not in TERMINAL:
            raise ApifyError(f"The Apify run took over {RUN_TIMEOUT // 60} minutes. It's still running in Apify (run {run['id']}).")
        if status != "SUCCEEDED":
            raise ApifyError(f"The Apify run ended as {status}. {run.get('statusMessage') or ''}".strip())
        # Some actors "succeed" with no results when the account hits a plan limit, and only say so
        # in the status message (e.g. "free user run limit exceeded").
        note = (run.get("statusMessage") or "").lower()
        if "limit exceeded" in note or "upgrade" in note:
            raise ApifyError(
                f"Apify stopped the run: {run.get('statusMessage')}. The actor's free runs are used up; "
                "a paid Apify plan (or the actor's paid tier) is needed.",
                402,
            )
        items = await self._request(
            "GET", f"/datasets/{run['defaultDatasetId']}/items", params={"clean": "true", "format": "json", "limit": max_items}
        )
        cost = run.get("usageTotalUsd")
        return RunResult(items=items if isinstance(items, list) else [], cost_usd=float(cost) if cost is not None else None, run_id=run.get("id"))

    # ---- Actors ----

    async def company_employees(
        self, company: str, job_titles: list[str], max_items: int
    ) -> tuple[list[Employee], RunResult]:
        """People at one company on LinkedIn. `company` is a LinkedIn company URL or the company's name."""
        actor_input: dict[str, Any] = {
            "companies": [company],
            "profileScraperMode": "Short ($4 per 1k)",
            "maxItems": max_items,
            "companyBatchMode": "all_at_once",
        }
        if job_titles:
            actor_input["jobTitles"] = job_titles
        result = await self.run(EMPLOYEES_ACTOR, actor_input, max_items)
        people = []
        for item in result.items:
            url = item.get("linkedinUrl")
            key = item.get("id") or item.get("publicIdentifier") or linkedin_slug(url)
            if not key:
                continue
            position = (item.get("currentPosition") or [{}])[0] or {}
            people.append(
                Employee(
                    external_id=str(key),
                    first_name=item.get("firstName") or "",
                    last_name=item.get("lastName") or "",
                    title=position.get("position") or item.get("headline") or "",
                    linkedin_url=url,
                    company_name=position.get("companyName"),
                    raw=item,
                )
            )
        return people, result

    async def linkedin_posts(self, profile_urls: list[str], max_posts: int, since: datetime) -> tuple[list[FetchedPost], RunResult]:
        """Recent posts by these LinkedIn profiles."""
        actor_input = {
            "targetUrls": profile_urls,
            "maxPosts": max_posts,
            "postedLimitDate": since.strftime("%Y-%m-%d"),
            "includeQuotePosts": True,
            "includeReposts": True,
            "scrapeReactions": False,
            "scrapeComments": False,
        }
        result = await self.run(LINKEDIN_POSTS_ACTOR, actor_input, max_posts * len(profile_urls) + 10)
        posts = []
        for item in result.items:
            if item.get("type") not in (None, "post") or not item.get("linkedinUrl"):
                continue
            author = item.get("author") or {}
            media = "image" if item.get("postImages") else "article" if item.get("document") or item.get("article") else "text"
            # `query` says which input URL a post came from: more reliable than the author, who is
            # someone else on a repost.
            query = item.get("query")
            query_url = query if isinstance(query, str) else next(
                (v for v in (query or {}).values() if isinstance(v, str) and "linkedin.com" in v), None
            )
            header = item.get("header")
            header_text = (header.get("text") if isinstance(header, dict) else header) or ""
            posts.append(
                FetchedPost(
                    source="linkedin",
                    url=item["linkedinUrl"],
                    external_id=str(item["id"]) if item.get("id") else None,
                    posted_at=_date(item.get("postedAt")),
                    text=item.get("content") or "",
                    media_type=media,
                    # The actor has no explicit repost flag; a repost is a post by someone else.
                    is_repost=bool(item.get("repost") or item.get("isRepost") or item.get("resharedPost"))
                    or "repost" in str(header_text).lower(),
                    owner_key=(
                        linkedin_slug(query_url)
                        or author.get("publicIdentifier")
                        or linkedin_slug(author.get("linkedinUrl"))
                        or ""
                    ).lower()
                    or None,
                    raw=item,
                )
            )
        return posts, result

    async def instagram_posts(self, profile_urls: list[str], max_posts: int, since: datetime) -> tuple[list[FetchedPost], RunResult]:
        """Recent posts from these public Instagram profiles."""
        actor_input = {
            "directUrls": profile_urls,
            "resultsType": "posts",
            "resultsLimit": max_posts,
            "onlyPostsNewerThan": since.strftime("%Y-%m-%d"),
            "addParentData": False,
        }
        result = await self.run(INSTAGRAM_ACTOR, actor_input, max_posts * len(profile_urls) + 10)
        kinds = {"Image": "image", "Video": "video", "Sidecar": "carousel"}
        posts = []
        for item in result.items:
            if not item.get("url") or item.get("error"):
                continue
            posts.append(
                FetchedPost(
                    source="instagram",
                    url=item["url"],
                    external_id=str(item["id"]) if item.get("id") else None,
                    posted_at=_date(item.get("timestamp")),
                    text=item.get("caption") or "",
                    media_type=kinds.get(item.get("type") or "", "image"),
                    is_repost=False,
                    owner_key=(item.get("ownerUsername") or instagram_handle(item.get("inputUrl")) or "").lower() or None,
                    raw=item,
                )
            )
        return posts, result
