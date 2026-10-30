"""The cost estimator's starting point, until a workspace saves its own: one line per operation that costs
money, plus the subscriptions.

Only prices with a source are filled in: Claude from tokens measured on 30 Oct 2026 and Coldbrew's model
price table (services/claude.py), Google Maps from Coldbrew's own table (services/google_places.py), and the
CheapSequencer and CheapInboxes plans. Everything else starts at $0 with a note saying what to enter.
Quantities are only filled in where Coldbrew's own process fixes them (one email and two follow-ups per
lead, one reveal per lead, five posts per source by default); the rest start at 0.
"""

from typing import Any

from services.claude import PRICES
from services.google_places import CENTER_COST_USD, SEARCH_COST_USD

CLAUDE_MODEL = "claude-haiku-5-5"


def claude_price(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES[CLAUDE_MODEL])
    return round((tokens_in * price_in + tokens_out * price_out) / 1e6, 6)


def _claude(id_: str, name: str, unit: str, action: str | None, tokens_in: int, tokens_out: int, quantity: float, basis: str,
            note: str) -> dict[str, Any]:  # fmt: skip
    return {"id": id_, "name": name, "category": "AI (Claude)", "unit_label": unit, "model": CLAUDE_MODEL,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "price_usd": claude_price(CLAUDE_MODEL, tokens_in, tokens_out),
            "quantity": quantity, "basis": basis, "action": action, "note": note, "enabled": True}  # fmt: skip


def _item(id_: str, name: str, category: str, unit: str, price: float, quantity: float, basis: str, note: str) -> dict[str, Any]:
    return {"id": id_, "name": name, "category": category, "unit_label": unit, "price_usd": price, "quantity": quantity,
            "basis": basis, "model": None, "tokens_in": None, "tokens_out": None, "action": None, "note": note, "enabled": True}  # fmt: skip


DEFAULT_ESTIMATE: dict[str, Any] = {
    "leads_per_month": 2000,
    "emails_per_lead": 3,
    "reply_rate_pct": 0,
    "items": [
        # Writing (Claude)
        _claude("email-generation", "Email generation (hooks, email, score)", "per lead written", "Write emails", 10182, 2562, 1, "lead",
                "Tokens per lead measured on 30 Oct 2026 (2 leads). More posts or a bigger model cost more."),
        _claude("follow-up", "Follow-up writing", "per follow-up", "Write follow-ups", 2017, 933, 2, "lead",
                "Measured on 30 Oct 2026. Only written for leads who haven't replied, so fewer with a high reply rate."),
        _claude("verification", "Company verification (research score)", "per company", "Research companies", 4992, 386, 0, "month",
                "Measured on 30 Oct 2026 (1 company). Enter how many companies you research a month."),
        _claude("prompt-improvement", "Prompt improvement", "per run", "Improve prompt", 0, 0, 0, "month",
                "No runs measured yet: enter tokens once you've used it, or leave at 0."),
        # Finding companies
        _item("maps-search", "Google Maps search", "Finding companies", "per search", SEARCH_COST_USD, 0, "month",
              "Coldbrew's price table. Each search term in each place is one search: enter searches a month."),
        _item("maps-location", "Google Maps location lookup", "Finding companies", "per place looked up", CENTER_COST_USD, 0, "month",
              "Coldbrew's price table. One lookup per place searched around."),
        _item("apollo-companies", "Apollo company search", "Finding companies", "per page", 0, 0, "month",
              "Enter your Apollo plan's cost per search page."),
        # Finding people
        _item("apollo-people", "Apollo people search", "Finding people", "per search", 0, 0, "month",
              "Enter your Apollo plan's cost per search."),
        _item("apify-linkedin-people", "LinkedIn people search (Apify)", "Finding people", "per profile", 0, 0, "month",
              "Enter Apify's price per profile for the LinkedIn people actor."),
        _item("apollo-reveal", "Email reveal (Apollo)", "Finding people", "per email revealed", 0, 1, "lead",
              "One Apollo credit per revealed email: enter your plan's price per credit."),
        # Research and posts
        _item("instagram-profile", "Instagram profile check (Apify)", "Research and posts", "per profile", 0, 0, "month",
              "Research reads a company's Instagram profile: enter Apify's price per profile and companies a month."),
        _item("website-pages", "Website reading (Jina)", "Research and posts", "per page", 0, 0, "month",
              "Free up to Jina's rate limit without a key; enter your plan's price per page if you pay."),
        _item("linkedin-posts", "LinkedIn posts (Apify)", "Research and posts", "per post", 0, 5, "lead",
              "5 posts per lead is the default in Settings. Enter Apify's price per post."),
        _item("instagram-posts", "Instagram posts (Apify)", "Research and posts", "per post", 0, 5, "lead",
              "5 posts per lead is the default in Settings. Enter Apify's price per post."),
        # Subscriptions
        _item("cheapsequencer", "CheapSequencer Starter", "Sending", "per month", 29, 1, "month",
              "10,000 campaign sends a month; warmup is free and unlimited. CheapSequencer's published price."),
        _item("cheapinboxes", "CheapInboxes (16 mailboxes)", "Sending", "per month", 56, 1, "month",
              "From your CheapInboxes billing on 30 Oct 2026."),
        _item("railway", "Railway (API, worker, beat, Redis)", "Hosting", "per month", 0, 1, "month", "Enter your Railway bill."),
        _item("vercel", "Vercel (frontend)", "Hosting", "per month", 0, 1, "month", "Enter your Vercel plan."),
        _item("supabase", "Supabase (database and sign-in)", "Hosting", "per month", 0, 1, "month", "Enter your Supabase plan."),
    ],
}

_LEGACY_UNIT_LABEL = {"month": "per month", "lead": "per lead", "email": "per email sent"}


def upgrade(saved: dict[str, Any]) -> dict[str, Any]:
    """An estimate saved before items had a price and a quantity (one amount per month, lead or email)."""
    items = []
    for item in saved.get("items", []):
        if "price_usd" not in item:
            unit = item.get("unit", "month")
            item = {**{k: v for k, v in item.items() if k not in ("unit", "amount_usd")}, "price_usd": item.get("amount_usd", 0),
                    "quantity": 1, "basis": unit, "unit_label": _LEGACY_UNIT_LABEL.get(unit, "per month"),
                    "model": None, "tokens_in": None, "tokens_out": None, "action": None}  # fmt: skip
        items.append(item)
    return {**saved, "items": items}
