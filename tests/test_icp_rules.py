"""The ICP rule check must answer like Lead Intel's matcher (packages/core/src/icp/match.ts)."""

from services.icp_rules import Evidence, check, contains

SCREEN = {  # Lead Intel's own example rule
    "name": "Screen printing", "industry": "Printing", "sources": [],
    "any_of": ["screen print", "screenprint", "silkscreen", "silk screen"], "none_of": ["equipment", "supplies only"],
    "website_any_of": [], "instagram_any_of": [], "require_all": False, "min_followers": None, "max_followers": None,
    "requires_website": False, "min_reviews": None, "places": [],
}  # fmt: skip


def rule(**kw):
    return SCREEN | kw


def test_term_matches_at_a_word_edge_like_lead_intel() -> None:
    # Lead Intel's own cases: dtfdallas.com, bestpricedtf.com and dtf-dallas.com are all DTF businesses.
    assert contains("dtfdallas.com", "dtf") and contains("bestpricedtf.com", "dtf") and contains("dtf-dallas.com", "dtf")
    assert not contains("widtfoo", "dtf")
    assert contains("We do Direct to Film", "direct to film")  # phrases match as plain text
    assert contains("teeth whitening", "tee")  # the documented cost of the looseness


def test_fits_on_website_words() -> None:
    r = check(SCREEN, Evidence(website_text="Same day screen printing in Chicago", has_website=True))
    assert r.fit and "screen print" in r.reason and r.hits[0]["source"] == "website_text"


def test_exclusion_is_checked_everywhere_the_rule_reads() -> None:
    ev = Evidence(instagram_bio="Screen printing shop", website_text="We sell screen printing equipment")
    r = check(SCREEN, ev)
    assert not r.fit and '"equipment"' in r.reason


def test_nothing_read_is_no_match_not_a_guess() -> None:
    r = check(SCREEN, Evidence(business_name="Acme", has_website=False))
    assert not r.fit and "None of the ICP's words" in r.reason


def test_require_all_needs_both_sides() -> None:
    both = rule(any_of=[], website_any_of=["shop", "collection"], instagram_any_of=["clothing", "apparel"], require_all=True)
    assert check(both, Evidence(website_text="Shop the collection", instagram_category="Clothing (Brand)")).fit
    only_site = check(both, Evidence(website_text="Shop the collection"))
    assert not only_site.fit and "on their Instagram" in only_site.reason


def test_follower_gate_fails_when_followers_unknown() -> None:
    gated = rule(min_followers=500)
    assert not check(gated, Evidence(website_text="screen printing")).fit
    assert not check(gated, Evidence(website_text="screen printing", instagram_followers=100)).fit
    assert check(gated, Evidence(website_text="screen printing", instagram_followers=900)).fit


def test_requires_website() -> None:
    r = check(rule(requires_website=True), Evidence(instagram_bio="screen printing", has_website=False))
    assert not r.fit and "website" in r.reason


def test_reviews_and_search_places_are_skipped_not_failed() -> None:
    r = check(rule(min_reviews=20, places=["Texas"]), Evidence(website_text="screen printing"), places_by_search=True)
    assert r.fit and len(r.skipped) == 2


def test_places_checked_when_not_applied_by_search() -> None:
    assert not check(rule(places=["Texas"]), Evidence(website_text="screen printing", city="Chicago", state="IL")).fit
    assert check(rule(places=["Texas"]), Evidence(website_text="screen printing", city="Dallas", state="Texas")).fit


def test_rule_without_words_needs_a_condition() -> None:
    assert not check(rule(any_of=[], none_of=[]), Evidence(website_text="anything")).fit
    assert check(rule(any_of=[], none_of=[], min_followers=10), Evidence(instagram_followers=50)).fit
