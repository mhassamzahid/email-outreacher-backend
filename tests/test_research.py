"""Research and scoring, with fake website reader, Apify and Claude. No network, no cost."""

import asyncio
import functools
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx

from models import Company
from services.apify import InstagramProfile, RunResult
from services.research import research_companies
from services.scoring import CompanyScore, CompanyScorer, build_prompt
from services.website import SiteRead

ICP = {
    "name": "Screen printing", "industry": "Printing", "sources": [], "any_of": ["screen print"],
    "none_of": ["equipment"], "website_any_of": [], "instagram_any_of": [], "require_all": False,
    "min_followers": None, "max_followers": None, "requires_website": False, "min_reviews": None,
    "places": ["Chicago"], "search_terms": ["screen printing"],
}  # fmt: skip


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class FakeReader:
    def __init__(self, sites: dict[str, SiteRead]) -> None:
        self.sites = sites
        self.read_calls: list[str] = []

    async def read(self, domain):
        self.read_calls.append(domain)
        return self.sites.get(domain, SiteRead(domain=domain, outcome="unreachable", detail="down"))


class FakeApify:
    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    async def instagram_profiles(self, handles):
        self.asked.append(handles)
        profiles = {h: InstagramProfile(h, h.title(), "Custom tees", "Printing service", 1200, False, False, None, {}) for h in handles}
        return profiles, RunResult([], 0.0026 * len(handles), "run-ig")


USAGE = SimpleNamespace(input_tokens=850, output_tokens=95)


class FakeScorer:
    def __init__(self, score: int = 82, error: str | None = None) -> None:
        self.calls = 0
        self.value, self.error = score, error

    async def score(self, icp, profile, evidence):
        from services.scoring import ScoreResult

        self.calls += 1
        self.last_evidence = evidence
        return ScoreResult(None if self.error else self.value, "Prints shirts for local teams", ["Website offers screen printing"],
                           [], "claude-haiku-5-5", self.error, input_tokens=900, output_tokens=120)  # fmt: skip


def company(name, domain):
    return Company(id=uuid4(), workspace_id=uuid4(), icp_id=uuid4(), source="apollo", name=name, domain=domain,
                   status="found", overridden=False, raw={"industry": "printing"})  # fmt: skip


def site(domain, text, socials=None):
    return SiteRead(domain=domain, outcome="ok", via="own_crawl", resolved_url=f"https://{domain}/", text=text,
                    title=domain, socials=socials or {})  # fmt: skip


@sync
async def test_research_reads_checks_scores_and_decides() -> None:
    good, wrong, sells_machines, down = (
        company("Ink Co", "inkco.com"), company("Bake Co", "bakeco.com"),
        company("Press Supply", "presssupply.com"), company("Down Co", "down.com"),
    )  # fmt: skip
    reader = FakeReader({
        "inkco.com": site("inkco.com", "Same day screen printing", {"instagram": "https://instagram.com/inkco"}),
        "bakeco.com": site("bakeco.com", "Fresh bread daily"),
        "presssupply.com": site("presssupply.com", "Screen printing equipment for sale"),
    })  # fmt: skip
    apify, scorer = FakeApify(), FakeScorer(82)
    summary = await research_companies([good, wrong, sells_machines, down], ICP, None, reader, apify, scorer, 60)

    assert (summary.qualified, summary.not_fit, summary.errors) == (1, 3, 0)
    assert good.status == "qualified" and good.score == 82 and good.status_reason.startswith("Scored 82")
    assert good.instagram_url == "https://instagram.com/inkco"
    assert good.research["instagram"]["followers"] == 1200 and good.research["website"]["via"] == "own_crawl"
    assert wrong.status == "not_fit" and "None of the ICP's words" in wrong.status_reason
    assert sells_machines.status == "not_fit" and '"equipment"' in sells_machines.status_reason
    assert down.status == "not_fit" and "couldn't be read" in down.status_reason
    # Places were applied by Apollo's search, so they're skipped, not failed.
    assert good.rule_detail["skipped"]
    # Only the company that passed the rule was sent to Claude; Instagram read once for the batch.
    assert scorer.calls == 1 and apify.asked == [["inkco"]]
    assert summary.apify_cost_usd == 0.0026
    assert (summary.claude_calls, summary.input_tokens, summary.output_tokens) == (1, 900, 120)
    assert summary.claude_model == "claude-haiku-5-5"


@sync
async def test_low_score_is_not_a_fit_and_overrides_stick() -> None:
    low, kept = company("Ink Co", "inkco.com"), company("Kept Co", "keptco.com")
    kept.overridden, kept.status = True, "qualified"
    reader = FakeReader({"inkco.com": site("inkco.com", "screen printing"), "keptco.com": site("keptco.com", "bakery")})
    await research_companies([low, kept], ICP, None, reader, None, FakeScorer(40), 60)
    assert low.status == "not_fit" and low.status_reason.startswith("Scored 40, under 60")
    assert kept.status == "qualified" and kept.rule_fit is False  # research recorded, decision kept


@sync
async def test_without_claude_the_rule_decides_and_scoring_errors_are_errors() -> None:
    a = company("Ink Co", "inkco.com")
    await research_companies([a], ICP, None, FakeReader({"inkco.com": site("inkco.com", "screen printing")}), None, None, 60)
    assert a.status == "qualified" and "Not scored" in a.status_reason
    b = company("Ink Co", "inkco.com")
    await research_companies([b], ICP, None, FakeReader({"inkco.com": site("inkco.com", "screen printing")}), None,
                             FakeScorer(error="Claude is rate-limiting us; try again shortly."), 60)  # fmt: skip
    assert b.status == "error" and "rate-limiting" in b.status_reason


def test_prompt_carries_icp_and_evidence_verbatim() -> None:
    prompt = build_prompt(ICP, {"description": "Local print shops", "pains": "Slow quotes", "offer": "Faster quoting"},
                          {"name": "Ink Co", "website": {"text": "Same day screen printing"}})  # fmt: skip
    assert "<icp>" in prompt and "Faster quoting" in prompt and "Same day screen printing" in prompt


@sync
async def test_scorer_uses_haiku_low_effort_and_structured_output() -> None:
    seen = {}

    async def parse(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn", usage=USAGE, parsed_output=CompanyScore(score=130, summary=" Fits ", reasons=["a", " "], concerns=[]))

    fake = SimpleNamespace(messages=SimpleNamespace(parse=parse), close=lambda: asyncio.sleep(0))
    scorer = CompanyScorer("key", "claude-haiku-5-5", client=fake)
    result = await scorer.score(ICP, None, {"name": "Ink Co"})
    assert seen["model"] == "claude-haiku-5-5" and seen["output_config"] == {"effort": "low"}
    assert seen["output_format"] is CompanyScore
    assert (result.score, result.summary, result.reasons) == (100, "Fits", ["a"])  # clamped and tidied
    assert (result.input_tokens, result.output_tokens) == (850, 95)


@sync
async def test_scorer_reports_refusals_and_api_errors() -> None:
    async def refuse(**kwargs):
        return SimpleNamespace(stop_reason="refusal", usage=USAGE, parsed_output=None)

    fake = SimpleNamespace(messages=SimpleNamespace(parse=refuse), close=lambda: asyncio.sleep(0))
    assert "declined" in (await CompanyScorer("k", "m", client=fake).score(ICP, None, {})).error

    async def limited(**kwargs):
        raise anthropic.RateLimitError("slow down", response=httpx.Response(429, request=httpx.Request("POST", "https://x")), body=None)

    fake = SimpleNamespace(messages=SimpleNamespace(parse=limited), close=lambda: asyncio.sleep(0))
    assert "rate-limiting" in (await CompanyScorer("k", "m", client=fake).score(ICP, None, {})).error
