"""Sample data for reviewing the app. Fictional companies and people on .example domains.

    python -m scripts.sample_data seed     # add it (skipped if it's already there)
    python -m scripts.sample_data delete   # remove all of it

Only `outreach.eo_*` tables are written. Lead Intel's tables are never touched: the sample rows only
reference an existing Lead Intel ICP id, as real rows do.

Every sample row is marked so `delete` removes exactly these and nothing else:
  eo_companies.raw, eo_stakeholders.raw and eo_leads.extra carry {"sample": true}; eo_provider_calls
  keys start with "sample:". Pages, posts, points, drafts and ratings hang off those rows and are
  removed with them (foreign keys cascade).
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from database import get_sessionmaker
from models import (
    Company,
    CompanyPage,
    Email,
    EmailFeedback,
    Insight,
    Lead,
    Post,
    Prompt,
    PromptVersion,
    ProviderCall,
    Stakeholder,
)

WORKSPACE = UUID("d0b31801-ed10-4e67-b36f-1a4f39dba163")  # AheadTech360
ICP = UUID("58e3d972-13bf-4381-9674-e25038103e4c")  # Lead Intel's "Screen printing" ICP (referenced, never changed)
MARK = {"sample": True}
NOW = datetime.now(UTC)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


COMPANIES = [
    {
        "key": "inkwell",
        "name": "Inkwell Print Co. (sample)",
        "domain": "inkwellprint.example",
        "city": "Austin",
        "state": "TX",
        "status": "qualified",
        "score": 86,
        "summary": "A custom screen printing shop for local teams and small brands, with same-week turnaround.",
        "reasons": ["Website offers custom screen printing and embroidery", "Instagram bio says 'custom tees for Austin teams'"],
        "concerns": ["No pricing on the website"],
        "rule": "Matched 'screen printing' on their website",
        "site": "Inkwell Print Co. Custom screen printing and embroidery in Austin. Same-week turnaround on team shirts, "
        "merch for local bands and small clothing brands. Minimums from 24 pieces. Get a quote in one business day.",
        "about": "Started in a garage in 2014, Inkwell now runs four automatic presses and an embroidery machine.",
        "instagram": "inkwellprint",
        "followers": 4200,
        "posts": [
            ("Just pressed 400 jerseys for the Eastside youth league. Huge thanks to the coaches.", 6),
            ("New water-based inks are in. Softer feel, same bright colours.", 19),
        ],
    },
    {
        "key": "redcanyon",
        "name": "Red Canyon Apparel (sample)",
        "domain": "redcanyonapparel.example",
        "city": "Phoenix",
        "state": "AZ",
        "status": "qualified",
        "score": 71,
        "summary": "Likely a fit: a print and apparel shop serving schools, but most of the site is about retail.",
        "reasons": ["Offers screen printing for schools and events"],
        "concerns": ["Half the website sells their own clothing line", "Website text was thin"],
        "rule": "Matched 'screen print' on their website",
        "site": "Red Canyon Apparel. Shop our desert-inspired tees, or order custom screen printing for your school, "
        "church or event. Bulk discounts available.",
        "about": "",
        "instagram": "redcanyonapparel",
        "followers": 1800,
        "posts": [("Spirit week orders are open for fall. Order by the 15th to get them before homecoming.", 11)],
    },
    {
        "key": "bluepeak",
        "name": "Blue Peak Promotions (sample)",
        "domain": "bluepeakpromo.example",
        "city": "Denver",
        "state": "CO",
        "status": "not_fit",
        "score": None,
        "rule": "None of the ICP's words were found: the site sells promotional pens, mugs and tote bags only",
        "site": "Blue Peak Promotions. Branded pens, mugs, tote bags and trade show giveaways. Fast quotes.",
        "about": "",
        "instagram": None,
        "followers": None,
        "posts": [],
    },
    {
        "key": "harbor",
        "name": "Harbor Thread Works (sample)",
        "domain": "harborthread.example",
        "city": "Portland",
        "state": "ME",
        "status": "found",
        "score": None,
        "rule": None,
        "site": None,
        "about": "",
        "instagram": None,
        "followers": None,
        "posts": [],
    },
]

PEOPLE = [
    # company key, first, last, title, has lead, email status of the draft (None = no draft)
    ("inkwell", "Maya", "Torres", "Owner", True, "draft"),
    ("inkwell", "Ben", "Walsh", "Production Manager", True, "edited"),
    ("redcanyon", "Carla", "Nguyen", "Founder", True, "approved"),
    ("redcanyon", "Derek", "Hollis", "Sales Manager", True, "skipped"),
    ("redcanyon", "Priya", "Shah", "Operations Lead", False, None),
]

DRAFTS = {
    "Maya": {
        "post": "Excited to share we just opened our second shop in Round Rock. More presses, faster turnaround for teams.",
        "post_days": 4,
        "points": [
            ("Opened a second shop in Round Rock", "More capacity usually means more quoting work", 5),
            ("Printed 400 jerseys for a youth league", "Team orders are their core business", 4),
            ("Switched to water-based inks", "They care about product quality", 2),
        ],
        "hook": "Congrats on opening the Round Rock shop.",
        "relevance": 5,
        "subject": "round rock shop",
        "body": "Hi Maya,\n\nCongrats on opening the Round Rock shop. Two locations usually means twice the quote requests, "
        "and team orders don't wait.\n\nWe help print shops turn quotes around in minutes instead of days, so more of "
        "those requests become orders.\n\nWorth a quick call next week?\n\nAhmed",
        "confidence": 82,
        "flags": [],
    },
    "Ben": {
        "post": "Hiring two press operators for our second shift. DM me if you know someone good.",
        "post_days": 9,
        "points": [("Hiring press operators for a second shift", "They're growing production", 4)],
        "hook": "Saw you're adding a second shift.",
        "relevance": 4,
        "subject": "second shift",
        "body": "Hi Ben,\n\nSaw you're adding a second shift at Inkwell. When production grows that fast, quoting is "
        "usually the bottleneck.\n\nWe help print shops send accurate quotes in minutes. Open to a 15-minute call?\n\nAhmed",
        "edited": "Hi Ben,\n\nSaw you're hiring for a second shift at Inkwell. Congrats on the growth.\n\nWhen production "
        "scales that fast, quotes are often what slows things down. We help shops like yours send accurate quotes in "
        "minutes.\n\nOpen to a quick call this week?\n\nAhmed",
        "confidence": 74,
        "flags": [],
    },
    "Carla": {
        "post": "Spirit week orders are open for fall. Order by the 15th to get them before homecoming.",
        "post_days": 11,
        "company_post": True,
        "points": [
            ("Fall spirit week orders are open", "Seasonal rush means lots of quotes at once", 4),
            ("Runs a retail clothing line too", "Less relevant to quoting", 1),
        ],
        "hook": "Saw Red Canyon's spirit week orders just opened.",
        "relevance": 4,
        "subject": "homecoming rush",
        "body": "Hi Carla,\n\nSaw Red Canyon's spirit week orders just opened. The homecoming rush is a lot of school "
        "quotes landing at once.\n\nWe help print shops answer those quotes the same day. Would a short call next week "
        "make sense?\n\nAhmed",
        "confidence": 78,
        "flags": [],
    },
    "Derek": {
        "post": None,
        "points": [],
        "hook": "",
        "relevance": 0,
        "subject": "Partnership opportunity with AheadTech360",
        "body": "Dear Derek,\n\nI hope this email finds you well. We are a leading agency helping businesses like yours "
        "unlock growth through innovative solutions. I would love to explore a partnership.\n\nBest regards,\nAhmed",
        "confidence": 35,
        "flags": ["no_hook"],
    },
}

FEEDBACK = {
    "Carla": ("good", ["strong_hook", "send_as_is"], "Short and tied to something real. More like this."),
    "Derek": ("bad", ["too_generic", "tone_off"], "Reads like a template. Never open with 'I hope this email finds you well'."),
}

USAGE = [
    # provider, endpoint, action, credits, cost, model, tokens in/out, days ago
    ("apollo", "mixed_companies/search", "Find companies", 1, 0, None, None, None, 3),
    ("apify", "apify~instagram-profile-scraper", "Research companies", None, Decimal("0.0104"), None, None, None, 3),
    ("claude", "messages", "Research companies", None, Decimal("0.0021"), "claude-haiku-5-5", 14200, 1300, 3),
    ("apollo", "mixed_people/api_search", "Find stakeholders", 0, 0, None, None, None, 2),
    ("apollo", "people/match", "Reveal email", 1, 0, None, None, None, 2),
    ("apollo", "people/match", "Reveal email", 1, 0, None, None, None, 2),
    ("apify", "harvestapi~linkedin-profile-posts", "Fetch posts", None, Decimal("0.0080"), None, None, None, 1),
    ("claude", "messages", "Write emails", None, Decimal("0.0032"), "claude-haiku-5-5", 21000, 2200, 1),
]


async def exists(s) -> bool:
    return bool(await s.scalar(select(func.count()).select_from(Lead).where(Lead.extra["sample"].as_boolean())))


async def seed() -> None:
    async with get_sessionmaker()() as s:
        if await exists(s):
            print("Sample data is already there. Run 'delete' first to start over.")
            return
        prompt_version = await s.scalar(
            select(PromptVersion.id)
            .join(Prompt, Prompt.current_version_id == PromptVersion.id)
            .where(Prompt.workspace_id == WORKSPACE, Prompt.kind == "email", Prompt.is_default)
        )

        companies: dict[str, Company] = {}
        for c in COMPANIES:
            researched = c["status"] != "found"
            row = Company(
                id=uuid4(), workspace_id=WORKSPACE, icp_id=ICP, source="apollo", apollo_org_id=f"sample-{c['key']}",
                name=c["name"], domain=c["domain"], website_url=f"https://{c['domain']}/", city=c["city"], state=c["state"],
                instagram_url=f"https://www.instagram.com/{c['instagram']}" if c["instagram"] else None,
                status=c["status"], overridden=False,
                status_reason=(
                    f"Scored {c['score']}: {c['summary']}" if c["score"] else c["rule"] if c["rule"] else None
                ),
                rule_fit=None if not researched else c["status"] == "qualified",
                rule_detail=None if not researched else {"reason": c["rule"], "hits": [], "skipped": ["places: Apollo's search applied them"]},
                score=c["score"],
                score_detail=None if not c["score"] else {
                    "summary": c["summary"], "reasons": c["reasons"], "concerns": c["concerns"], "model": "claude-haiku-5-5",
                    "error": None, "prompt_version_id": None, "input_tokens": 1800, "output_tokens": 160,
                },
                research=None if not researched else {
                    "website": {"outcome": "ok", "via": "own_crawl", "url": f"https://{c['domain']}/", "title": c["name"],
                                "text": c["site"], "detail": None,
                                "pages": [{"url": f"https://{c['domain']}/", "role": "homepage", "status": 200}]},
                    "socials": {"instagram": f"https://www.instagram.com/{c['instagram']}"} if c["instagram"] else {},
                    "instagram": None if not c["instagram"] else {
                        "handle": c["instagram"], "name": c["name"], "bio": "Custom tees, team gear and merch",
                        "category": "Printing service", "followers": c["followers"], "private": False, "verified": False,
                        "raw": MARK,
                    },
                },
                researched_at=ago(3) if researched else None,
                raw={**MARK, "industry": "printing", "estimated_num_employees": 12},
            )  # fmt: skip
            s.add(row)
            companies[c["key"]] = row
        await s.flush()

        for c in COMPANIES:
            row = companies[c["key"]]
            if c["site"]:
                s.add(CompanyPage(workspace_id=WORKSPACE, company_id=row.id, url=f"https://{c['domain']}/", role="homepage",
                                  via="own_crawl", status=200, text=c["site"]))  # fmt: skip
            if c["about"]:
                s.add(CompanyPage(workspace_id=WORKSPACE, company_id=row.id, url=f"https://{c['domain']}/about",
                                  role="about", via="own_crawl", status=200, text=c["about"]))  # fmt: skip
            for i, (text, days) in enumerate(c["posts"], 1):
                s.add(Post(workspace_id=WORKSPACE, company_id=row.id, source="instagram", media_type="image",
                           url=f"https://www.instagram.com/p/sample-{c['key']}-{i}/", text=text, posted_at=ago(days),
                           raw=MARK))  # fmt: skip

        for n, (key, first, last, title, has_lead, status) in enumerate(PEOPLE, 1):
            company = companies[key]
            email = f"{first.lower()}@{company.domain}"
            lead = None
            if has_lead:
                lead = Lead(
                    id=uuid4(), workspace_id=WORKSPACE, lead_intel_icp_id=ICP, first_name=first, last_name=last,
                    email=email, email_status="verified", title=title, company=company.name,
                    website=company.website_url, domain=company.domain,
                    linkedin_url=f"https://www.linkedin.com/in/sample-{first.lower()}-{last.lower()}",
                    extra=MARK, origin="apollo", external_id=f"sample-person-{n}", status="enriched",
                )  # fmt: skip
                s.add(lead)
                await s.flush()
            s.add(Stakeholder(
                workspace_id=WORKSPACE, company_id=company.id, lead_intel_icp_id=ICP, organization_name=company.name,
                source="apollo", external_id=f"sample-person-{n}", first_name=first, last_name=last, title=title,
                seniority="owner" if title in ("Owner", "Founder") else "manager",
                linkedin_url=f"https://www.linkedin.com/in/sample-{first.lower()}-{last.lower()}",
                has_email=True, email=email if has_lead else None, email_status="verified" if has_lead else None,
                revealed_at=ago(2) if has_lead else None, lead_id=lead.id if lead else None, raw=MARK, found_at=ago(2),
            ))  # fmt: skip
            if lead is None or status is None:
                continue

            d = DRAFTS[first]
            post = None
            if d.get("post"):
                owner = {"company_id": company.id} if d.get("company_post") else {"lead_id": lead.id}
                post = Post(id=uuid4(), workspace_id=WORKSPACE, source="instagram" if d.get("company_post") else "linkedin",
                            url=f"https://example.com/sample-post-{first.lower()}", text=d["post"],
                            posted_at=ago(d["post_days"]), media_type="text", raw=MARK, **owner)  # fmt: skip
                s.add(post)
                await s.flush()
            for rank, (point, why, relevance) in enumerate(d["points"], 1):
                s.add(Insight(workspace_id=WORKSPACE, lead_id=lead.id, post_id=post.id if post and rank == 1 else None,
                              rank=rank, point=point, why=why, relevance=relevance, model="claude-haiku-5-5"))  # fmt: skip
            row = Email(
                id=uuid4(), workspace_id=WORKSPACE, lead_id=lead.id, email_prompt_version_id=prompt_version,
                chosen_post_id=post.id if post else None, hook=d["hook"], relevance=d["relevance"], subject=d["subject"],
                body=d["body"], edited_body=d.get("edited"), confidence=d["confidence"], flags=d["flags"], status=status,
                cost_usd=Decimal("0.0008"), approved_at=ago(1) if status == "approved" else None,
            )  # fmt: skip
            s.add(row)
            lead.status = {"draft": "generated", "edited": "generated"}.get(status, status)
            await s.flush()
            if first in FEEDBACK:
                rating, tags, note = FEEDBACK[first]
                s.add(EmailFeedback(workspace_id=WORKSPACE, email_id=row.id, lead_intel_icp_id=ICP, rating=rating,
                                    tags=tags, note=note, subject=row.subject, body=row.edited_body or row.body, hook=row.hook))  # fmt: skip

        for i, (provider, endpoint, action, credits, cost, model, tin, tout, days) in enumerate(USAGE, 1):
            s.add(ProviderCall(
                idempotency_key=f"sample:{i}:{uuid4()}", workspace_id=WORKSPACE, provider=provider, endpoint=endpoint,
                status="succeeded", action=action, credits=credits, cost_usd=cost, model=model, input_tokens=tin,
                output_tokens=tout, called_at=ago(days),
            ))  # fmt: skip

        await s.commit()
        print(f"Added {len(COMPANIES)} companies, {len(PEOPLE)} people, {sum(p[4] for p in PEOPLE)} leads, "
              f"{len(DRAFTS)} drafts, {len(FEEDBACK)} ratings and {len(USAGE)} usage entries.")  # fmt: skip


async def remove() -> None:
    async with get_sessionmaker()() as s:
        # Stakeholders first: deleting a company would only unlink them.
        people = await s.execute(delete(Stakeholder).where(Stakeholder.raw["sample"].as_boolean()))
        leads = await s.execute(delete(Lead).where(Lead.extra["sample"].as_boolean()))  # posts, points, drafts, ratings
        companies = await s.execute(delete(Company).where(Company.raw["sample"].as_boolean()))  # pages, company posts
        calls = await s.execute(delete(ProviderCall).where(ProviderCall.idempotency_key.like("sample:%")))
        await s.commit()
        print(f"Deleted {companies.rowcount} companies, {people.rowcount} people, {leads.rowcount} leads "
              f"(with their posts, drafts and ratings) and {calls.rowcount} usage entries.")  # fmt: skip


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("seed", "delete"):
        sys.exit("Usage: python -m scripts.sample_data seed|delete")
    asyncio.run(seed() if command == "seed" else remove())
