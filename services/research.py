"""Researching Apollo companies against an ICP: read, check, score, decide.

For each company:
  1. read its website (our polite crawl, Jina as the fallback) and the socials it links to;
  2. read its Instagram profile, if the website links one (one Apify run for the whole batch);
  3. check the ICP's rule (Lead Intel's matcher, services/icp_rules.py);
  4. if it passes, score it with Claude (services/scoring.py);
  5. qualified when it passes the rule and scores at least the threshold; not a fit otherwise,
     with the reason. A company someone kept or dropped by hand keeps that decision.

Lead Intel's companies never come through here: Lead Intel already verified them.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from models import Company
from services.apify import ApifyClient, ApifyError, FetchedPost, InstagramProfile, instagram_handle
from services.icp_rules import Evidence, check
from services.scoring import CompanyScorer, PromptSpec
from services.website import SiteRead, WebsiteReader, normalize_domain

READ_CONCURRENCY = 3  # sites read at once; each site is still read one page at a time


@dataclass
class ResearchSummary:
    qualified: int = 0
    not_fit: int = 0
    errors: int = 0
    apify_cost_usd: float = 0.0
    # What was read, for the caller to store: full page texts and the company's Instagram posts.
    pages: dict[UUID, tuple[str, list[dict]]] = field(default_factory=dict)  # company id -> (via, pages)
    posts: dict[UUID, list[FetchedPost]] = field(default_factory=dict)
    claude_calls: int = 0
    claude_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    notes: list[str] = field(default_factory=list)


def _apollo_facts(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    keep = ("industry", "keywords", "short_description", "seo_description", "founded_year", "estimated_num_employees",
            "city", "state", "country")  # fmt: skip
    return {k: raw[k] for k in keep if raw.get(k) not in (None, "", [])}


def _evidence(company: Company, site: SiteRead | None, ig: InstagramProfile | None) -> Evidence:
    return Evidence(
        business_name=company.name,
        website_text=site.text if site and site.reached else None,
        website_domain=company.domain,
        instagram_bio=ig.bio if ig else None,
        instagram_category=ig.category if ig else None,
        instagram_handle=ig.handle if ig else None,
        instagram_followers=ig.followers if ig else None,
        has_website=bool(site and site.reached),
        city=company.city,
        state=company.state,
    )


def _score_evidence(company: Company, site: SiteRead | None, ig: InstagramProfile | None) -> dict[str, Any]:
    """What the Company Verification prompt's {{company.*}} placeholders are filled from."""
    return {
        "name": company.name,
        "domain": company.domain,
        "location": ", ".join(p for p in (company.city, company.state) if p),
        "socials": {k: v for k, v in {
            "instagram": company.instagram_url, "facebook": company.facebook_url,
            "linkedin": company.linkedin_url, "x": company.twitter_url,
        }.items() if v},
        "website_text": "\n".join(x for x in (site.title, site.text) if x) if site and site.reached else None,
        "instagram": None if ig is None else {
            "handle": ig.handle, "name": ig.full_name, "bio": ig.bio, "category": ig.category,
            "followers": ig.followers, "private": ig.private,
        },
        "apollo": _apollo_facts(company.raw),
    }  # fmt: skip


async def research_companies(
    companies: list[Company],
    icp: dict[str, Any],
    profile: dict[str, Any] | None,
    reader: WebsiteReader,
    apify: ApifyClient | None,
    scorer: CompanyScorer | None,
    threshold: int,
    prompt: PromptSpec | None = None,
) -> ResearchSummary:
    summary = ResearchSummary()
    gate = asyncio.Semaphore(READ_CONCURRENCY)

    async def read(company: Company) -> SiteRead | None:
        target = company.domain or normalize_domain(company.website_url)
        if not target:
            return None
        async with gate:
            return await reader.read(target)

    sites = await asyncio.gather(*(read(c) for c in companies))

    # Instagram: the account the company's own website links to (never a search).
    for company, site in zip(companies, sites, strict=True):
        if site and site.socials:
            company.instagram_url = company.instagram_url or site.socials.get("instagram")
            company.facebook_url = company.facebook_url or site.socials.get("facebook")
            company.linkedin_url = company.linkedin_url or site.socials.get("linkedin")
            company.twitter_url = company.twitter_url or site.socials.get("x")
    handles = {c.id: h for c in companies if (h := instagram_handle(c.instagram_url))}
    profiles: dict[str, InstagramProfile] = {}
    if handles and apify is not None:
        try:
            profiles, run = await apify.instagram_profiles(list(handles.values()))
            summary.apify_cost_usd += run.cost_usd or 0
        except ApifyError as exc:
            summary.notes.append(f"Instagram profiles weren't read: {exc.message}")
    elif handles:
        summary.notes.append("Instagram profiles weren't read: APIFY_TOKEN is not set")

    places_by_search = bool([p for p in icp.get("places") or [] if p.strip()])

    async def decide(company: Company, site: SiteRead | None) -> None:
        ig = profiles.get(handles.get(company.id, ""))
        rule = check(icp, _evidence(company, site, ig), places_by_search=places_by_search)
        company.rule_fit = rule.fit
        company.rule_detail = {"reason": rule.reason, "hits": rule.hits, "skipped": rule.skipped}
        company.research = {
            "website": None if site is None else {
                "outcome": site.outcome, "via": site.via, "url": site.resolved_url, "title": site.title,
                "text": site.text, "detail": site.detail,
                # Full page texts go to eo_company_pages; only where they came from is kept here.
                "pages": [{k: v for k, v in page.items() if k != "text"} for page in site.pages],
            },
            "socials": {k: v for k, v in {
                "instagram": company.instagram_url, "facebook": company.facebook_url,
                "linkedin": company.linkedin_url, "x": company.twitter_url,
            }.items() if v},
            "instagram": None if ig is None else {
                "handle": ig.handle, "name": ig.full_name, "bio": ig.bio, "category": ig.category,
                "followers": ig.followers, "private": ig.private, "verified": ig.verified, "raw": ig.raw,
            },
        }  # fmt: skip
        if site and site.reached and site.pages:
            summary.pages[company.id] = (site.via or "own_crawl", site.pages)
        if ig and ig.posts:
            summary.posts[company.id] = ig.posts
        company.researched_at = datetime.now(UTC)

        unread = "" if site and site.reached else " (their website couldn't be read)"
        if not rule.fit:
            status, reason = "not_fit", rule.reason + unread
            company.score, company.score_detail = None, None
        elif scorer is None:
            status, reason = "qualified", f"Passes the ICP's rule: {rule.reason}. Not scored: ANTHROPIC_API_KEY is not set"
        else:
            result = await scorer.score(icp, profile, _score_evidence(company, site, ig), prompt)
            summary.claude_calls += 1
            summary.claude_model = result.model
            summary.input_tokens += result.input_tokens
            summary.output_tokens += result.output_tokens
            company.score = result.score
            company.score_detail = {
                "summary": result.summary, "reasons": result.reasons, "concerns": result.concerns,
                "model": result.model, "error": result.error,
                "prompt_version_id": prompt.version_id if prompt else None,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            }  # fmt: skip
            if result.error:
                status, reason = "error", result.error
            elif result.score is not None and result.score >= threshold:
                status, reason = "qualified", f"Scored {result.score}: {result.summary}"
            else:
                status, reason = "not_fit", f"Scored {result.score}, under {threshold}: {result.summary}"

        if not company.overridden:
            company.status, company.status_reason = status, reason
        summary.qualified += status == "qualified"
        summary.not_fit += status == "not_fit"
        summary.errors += status == "error"

    await asyncio.gather(*(decide(c, s) for c, s in zip(companies, sites, strict=True)))
    return summary
