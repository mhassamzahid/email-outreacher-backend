"""Does a company fit an ICP's rule? Lead Intel's matcher (packages/core/src/icp/match.ts), for one ICP.

Same semantics, so a company gets the same answer here as in Lead Intel:
  - a term matches at the start or end of a word ("dtf" finds "bestpricedtf" but not "widtfoo");
    a phrase with spaces or punctuation matches as plain text;
  - "never these words" are checked across everything the rule reads;
  - filled-in word groups combine with any/all (`require_all`);
  - a condition that can't be answered fails ("at least 500 followers" doesn't pass for an
    account nobody looked at).

Two conditions can't be measured for companies found in Apollo, and are reported as skipped rather
than failed: Google reviews (we don't fetch listings), and places when the company was found with
the ICP's places as Apollo's location filter.

Pure: no database, no network.
"""

import re
from dataclasses import dataclass, field
from typing import Any

SOURCES = (
    "business_name", "website_text", "website_domain", "instagram_bio", "instagram_category", "instagram_handle",
    "meta_ads_text",
)  # fmt: skip
WEBSITE_SOURCES = ("website_text", "website_domain")
INSTAGRAM_SOURCES = ("instagram_bio", "instagram_category", "instagram_handle")
SOURCE_LABELS = {
    "business_name": "their business name",
    "website_text": "their website",
    "website_domain": "their web address",
    "instagram_bio": "their Instagram bio",
    "instagram_category": "their Instagram category",
    "instagram_handle": "their Instagram username",
    "meta_ads_text": "their ads",
}


@dataclass
class Evidence:
    business_name: str | None = None
    website_text: str | None = None
    website_domain: str | None = None
    instagram_bio: str | None = None
    instagram_category: str | None = None
    instagram_handle: str | None = None
    meta_ads_text: str | None = None
    instagram_followers: int | None = None
    # True: a website was read. False: none, or unreachable. None: never looked.
    has_website: bool | None = None
    review_count: int | None = None
    city: str | None = None
    state: str | None = None


@dataclass
class RuleResult:
    fit: bool
    # One plain sentence: why it fits, or the first thing that ruled it out.
    reason: str
    hits: list[dict[str, str]] = field(default_factory=list)  # [{term, source, group}]
    skipped: list[str] = field(default_factory=list)  # conditions that couldn't be measured


def contains(haystack: str, term: str) -> bool:
    needle = term.strip().lower()
    if not needle:
        return False
    hay = haystack.lower()
    if not re.fullmatch(r"[a-z0-9]+", needle):
        return needle in hay
    escaped = re.escape(needle)
    return re.search(rf"(?:(?:^|[^a-z0-9]){escaped})|(?:{escaped}(?:[^a-z0-9]|$))", hay) is not None


def _text(source: str, ev: Evidence) -> str | None:
    return getattr(ev, source, None)


def _first_hit(terms: list[str], sources: tuple[str, ...] | list[str], ev: Evidence, group: str) -> dict[str, str] | None:
    for source in sources:
        text = _text(source, ev)
        if not text or not text.strip():
            continue
        for term in terms:
            if contains(text, term):
                return {"term": term, "source": source, "group": group}
    return None


def check(rule: dict[str, Any], ev: Evidence, *, places_by_search: bool = False, reviews_measurable: bool = False) -> RuleResult:
    """Check one ICP (a Lead Intel icps row, snake_case keys) against what we know about a company."""
    skipped: list[str] = []
    min_f, max_f = rule.get("min_followers"), rule.get("max_followers")
    if min_f is not None or max_f is not None:
        followers = ev.instagram_followers
        if followers is None:
            return RuleResult(False, "The ICP sets an Instagram follower range, and we couldn't read their followers")
        if min_f is not None and followers < min_f:
            return RuleResult(False, f"{followers:,} Instagram followers, under the ICP's {min_f:,}")
        if max_f is not None and followers > max_f:
            return RuleResult(False, f"{followers:,} Instagram followers, over the ICP's {max_f:,}")

    if rule.get("requires_website") and ev.has_website is not True:
        return RuleResult(False, "The ICP needs a working website, and theirs couldn't be read")

    if rule.get("min_reviews") is not None:
        if not reviews_measurable:
            skipped.append("Google reviews (not checked for these companies)")
        elif ev.review_count is None or ev.review_count < rule["min_reviews"]:
            return RuleResult(False, f"Fewer than {rule['min_reviews']} Google reviews")

    places = [p for p in rule.get("places") or [] if p.strip()]
    if places:
        if places_by_search:
            skipped.append("places (already applied as the search's location filter)")
        else:
            where = ", ".join(p for p in (ev.city, ev.state) if p and p.strip()).lower()
            if not where or not any(p.strip().lower() in where for p in places):
                return RuleResult(False, f"Not in {' or '.join(places)}")

    sources = [s for s in rule.get("sources") or [] if s in SOURCES] or list(SOURCES)
    any_of = rule.get("any_of") or []
    website_any = rule.get("website_any_of") or []
    instagram_any = rule.get("instagram_any_of") or []

    reads = set(sources) | (set(WEBSITE_SOURCES) if website_any else set()) | (set(INSTAGRAM_SOURCES) if instagram_any else set())
    for term in rule.get("none_of") or []:
        for source in reads:
            text = _text(source, ev)
            if text and contains(text, term):
                return RuleResult(False, f'"{term}" appears in {SOURCE_LABELS[source]}, which the ICP excludes')

    groups = []
    if any_of:
        groups.append(("anywhere", any_of, sources))
    if website_any:
        groups.append(("website", website_any, list(WEBSITE_SOURCES)))
    if instagram_any:
        groups.append(("instagram", instagram_any, list(INSTAGRAM_SOURCES)))

    gated = min_f is not None or max_f is not None or rule.get("requires_website") or rule.get("min_reviews") is not None or places
    if not groups:
        # Lead Intel: a rule with no words only means something when a condition carries it.
        if not gated:
            return RuleResult(False, "The ICP has no words or conditions to check against", skipped=skipped)
        return RuleResult(True, "Meets the ICP's conditions", skipped=skipped)

    hits = []
    for group, terms, group_sources in groups:
        hit = _first_hit(terms, group_sources, ev, group)
        if hit:
            hits.append(hit)
        elif rule.get("require_all"):
            where = {"anywhere": "anywhere we read", "website": "on their website", "instagram": "on their Instagram"}[group]
            return RuleResult(False, f"None of the ICP's words found {where}", hits, skipped)

    if not hits:
        return RuleResult(False, "None of the ICP's words found on their website or Instagram", hits, skipped)
    first = hits[0]
    return RuleResult(True, f'"{first["term"]}" on {SOURCE_LABELS[first["source"]]}', hits, skipped)
