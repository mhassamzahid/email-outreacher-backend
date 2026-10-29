"""Instagram during research: handle parsing, retrying the website's handle, and reading Apify's real
cost. No network, no cost."""

import asyncio
import functools

import httpx

from services.apify import ApifyClient, InstagramProfile, RunResult, instagram_handle
from services.research import research_companies
from tests.test_research import ICP, FakeReader, FakeScorer, company, site


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def test_instagram_handle() -> None:
    assert instagram_handle("https://www.instagram.com/UpMerch/?hl=en") == "upmerch"
    assert instagram_handle("instagram.com/upmerch.hq") == "upmerch.hq"
    assert instagram_handle("@Ink_Co") == "ink_co"
    # Posts, Instagram's own pages, platform accounts and junk are nobody's profile.
    for bad in ("https://instagram.com/p/Cx1/", "https://www.instagram.com/explore/tags/tees/", "https://instagram.com/booksybiz",
                "@shopify", "not a handle!", "", None):  # fmt: skip
        assert instagram_handle(bad) is None, bad


class PickyApify:
    """Knows only some accounts, like Instagram after a rename."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.asked: list[list[str]] = []

    async def instagram_profiles(self, handles):
        self.asked.append(handles)
        found = {h: InstagramProfile(h, h, "Custom tees", None, 900, False, False, None, {}, []) for h in handles if h in self.known}
        return found, RunResult([], 0.0026, "run")


@sync
async def test_stale_handle_falls_back_to_the_websites() -> None:
    renamed = company("Up Merch", "upmerch.com")
    renamed.instagram_url = "https://www.instagram.com/upmerch/"  # stored, but Instagram doesn't know it
    gone = company("Gone Co", "goneco.com")
    gone.instagram_url = "https://instagram.com/goneco"  # unknown, and the website links nothing else
    reader = FakeReader({
        "upmerch.com": site("upmerch.com", "Screen printing and merch", {"instagram": "https://instagram.com/upmerch.hq"}),
        "goneco.com": site("goneco.com", "Screen printing shop"),
    })  # fmt: skip
    apify = PickyApify({"upmerch.hq"})
    summary = await research_companies([renamed, gone], ICP, None, reader, apify, FakeScorer(70), 60)

    assert apify.asked == [["upmerch", "goneco"], ["upmerch.hq"]]
    assert renamed.instagram_url == "https://www.instagram.com/upmerch.hq/"
    assert renamed.research["instagram"]["handle"] == "upmerch.hq" and gone.research["instagram"] is None
    # Both Apify runs are counted, and the one still missing is reported, not silent.
    assert summary.apify_cost_usd == 0.0052
    assert summary.notes == ["Instagram couldn't find @goneco (renamed, private or removed)."]


@sync
async def test_run_cost_is_read_again_when_apify_hasnt_filled_it_in() -> None:
    seen: list[str] = []

    def apify(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            # Finished, but the cost isn't on the run yet.
            return httpx.Response(201, json={"data": {"id": "r1", "status": "SUCCEEDED", "defaultDatasetId": "d1", "usageTotalUsd": 0}})
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json=[{"username": "inkco"}])
        return httpx.Response(200, json={"data": {"id": "r1", "status": "SUCCEEDED", "usageTotalUsd": 0.0026}})

    client = ApifyClient("token", transport=httpx.MockTransport(apify), poll_seconds=0)
    result = await client.run("apify~instagram-profile-scraper", {"usernames": ["inkco"]}, 5)
    await client.aclose()
    assert result.cost_usd == 0.0026 and result.items == [{"username": "inkco"}]
    assert seen[-1] == "GET /v2/actor-runs/r1"
