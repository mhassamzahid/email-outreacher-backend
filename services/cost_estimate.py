"""The cost estimator's starting point, until a workspace saves its own.

Only prices with a source are filled in; the rest start at $0 with a note saying where to find them.
"""

from typing import Any

DEFAULT_ESTIMATE: dict[str, Any] = {
    "leads_per_month": 2000,
    "emails_per_lead": 3,
    "reply_rate_pct": 0,
    "items": [
        {"id": "cheapsequencer", "name": "CheapSequencer Starter", "category": "Sending", "unit": "month", "amount_usd": 29,
         "note": "10,000 campaign sends a month; warmup is free and unlimited. CheapSequencer's published price.", "enabled": True},
        {"id": "cheapinboxes", "name": "CheapInboxes (16 mailboxes)", "category": "Sending", "unit": "month", "amount_usd": 56,
         "note": "From your CheapInboxes billing on 30 Oct 2026.", "enabled": True},
        {"id": "claude-email", "name": "Claude: first email", "category": "AI", "unit": "lead", "amount_usd": 0.0012,
         "note": "Hook selection, the email and its score. Measured on 30 Oct 2026 with Claude Haiku 5.5 for a lead with no "
                 "posts; more posts or a bigger model cost more.", "enabled": True},
        {"id": "claude-followups", "name": "Claude: two follow-ups", "category": "AI", "unit": "lead", "amount_usd": 0.0014,
         "note": "$0.0007 each, measured on 30 Oct 2026. Only written for leads who haven't replied.", "enabled": True},
        {"id": "apollo", "name": "Apollo email reveals", "category": "Data", "unit": "lead", "amount_usd": 0,
         "note": "Enter your Apollo plan's cost per revealed email.", "enabled": True},
        {"id": "apify", "name": "Apify (LinkedIn and Instagram posts)", "category": "Data", "unit": "lead", "amount_usd": 0,
         "note": "Enter your cost per lead from your Apify usage.", "enabled": True},
        {"id": "google-maps", "name": "Google Maps company search", "category": "Data", "unit": "month", "amount_usd": 0,
         "note": "Coldbrew counts about $0.035 per search: enter searches a month times that.", "enabled": True},
        {"id": "railway", "name": "Railway (API, worker, beat, Redis)", "category": "Hosting", "unit": "month", "amount_usd": 0,
         "note": "Enter your Railway bill.", "enabled": True},
        {"id": "vercel", "name": "Vercel (frontend)", "category": "Hosting", "unit": "month", "amount_usd": 0,
         "note": "Enter your Vercel plan.", "enabled": True},
        {"id": "supabase", "name": "Supabase (database and sign-in)", "category": "Hosting", "unit": "month", "amount_usd": 0,
         "note": "Enter your Supabase plan.", "enabled": True},
    ],
}  # fmt: skip
