"""Prompt templates: the defaults every workspace starts with, and how {{placeholders}} are filled.

Four kinds, each chosen per ICP (or the workspace's default of that kind):
  verify  Company Verification: scores how well a researched company fits the ICP.
  hook    Hook Selection: reads a person's posts and pulls out the points worth mentioning.
  email   Email Generation: writes the email from the ICP, the person and the hook.
  score   Email scoring: how safe a drafted email is to send without review (Settings).

Placeholders are looked up in a flat map; nothing is evaluated. Unknown ones render empty.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

KINDS = ("verify", "hook", "email", "score")
PAGE_CHARS = 4000  # per page, when pages are shown one by one
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
DEFAULT_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class DefaultPrompt:
    name: str
    system_prompt: str
    user_prompt: str
    temperature: float
    max_tokens: int
    model: str = DEFAULT_MODEL


VERIFY_SYSTEM = """You decide whether a company is a good prospect for an outreach campaign.

You get the campaign's ideal customer profile (ICP) and what we gathered about one company: text
from its website, its Instagram profile, its socials and Apollo's company data.

Score the fit from 0 to 100:
- 80 to 100: clearly the kind of business the ICP describes, with direct evidence.
- 60 to 79: likely a fit; the evidence points that way but leaves some doubt.
- 30 to 59: unclear or only partly matching.
- 0 to 29: a different kind of business, or one the ICP rules out.

Judge only from the evidence given. Don't assume facts that aren't there. When the evidence is
thin or missing, score lower and say what was missing. Reasons and concerns are short, specific,
and point to the evidence (for example "Website offers same-day screen printing")."""

VERIFY_USER = """The ICP
Name: {{icp.name}}
Industry: {{icp.industry}}
Who they are: {{icp.description}}
Their pains: {{icp.pains}}
What we offer: {{icp.offer}}
Matching rules from Lead Intel:
{{icp.rules}}

The company
Name: {{company.name}}
Website: {{company.domain}}
Location: {{company.location}}
Socials: {{company.socials}}

Apollo's company data:
{{company.apollo}}

Instagram profile:
{{company.instagram}}

Website text:
{{company.website}}

Score this company's fit with the ICP. Give a one-sentence summary, up to 4 reasons it fits and
up to 4 concerns, most important first."""

HOOK_SYSTEM = """You read a person's recent social posts and pull out what is worth mentioning in a
cold email to them. You are precise and conservative: a weak or risky hook is worse than no hook."""

HOOK_USER = """We sell to this ICP: {{icp.name}} ({{icp.titles}} in {{icp.industry}}).
Their main pains: {{icp.pains}}
Our offer: {{icp.offer}}
Hook guidance: {{icp.hookGuidance}}

Person: {{lead.firstName}} {{lead.lastName}}, {{lead.title}} at {{lead.company}}.

Their recent posts and their company's:
{{items}}

1. List the main points these posts make: what they're working on, launching, proud of or struggling with.
   Only work topics. One short line each, with the post number.
2. Pick the ONE point that best connects to our offer and their pains. Prefer recent, original posts
   over reposts.
3. Never pick grief, illness, layoffs, politics, religion or family. If those are the only points,
   pick none.

Return JSON only:
{
  "main_points": [{ "item": <post number>, "point": "<one line>" }],
  "chosen_item": <post number or null>,
  "focus": "<the point to build the email around, one sentence>",
  "hook": "<one natural sentence referencing it, max 25 words, no flattery, no quoting more than 5 words>",
  "relevance": <0-5>,
  "flags": ["sensitive_topic" | "repost" | "old_content"]
}
If nothing is relevant, return chosen_item null, relevance 0, focus "" and hook ""."""

EMAIL_SYSTEM = """You write short, plain-text cold emails that sound like one busy person writing to
another. No hype, no buzzwords, no exclamation marks, no em dashes, no "I hope this email finds you
well". Use contractions. Never invent facts, numbers or results."""

EMAIL_USER = """Write a cold email to {{lead.firstName}}, {{lead.title}} at {{lead.company}}.

ICP context
- Pains: {{icp.pains}}
- Offer: {{icp.offer}}
- Proof we can claim (use it exactly as written, or not at all): {{icp.proof}}
- Call to action: {{icp.cta}}
- Tone: {{icp.tone}}
- Never use these words: {{icp.wordsToAvoid}}

What to focus on (from their posts, may be empty): {{hook.summary}}
Hook (may be empty): {{hook.text}}
Source of hook: {{hook.source}}, {{hook.date}}

Example of an email in our voice:
{{icp.exampleEmail}}

Rules
- 60 to 110 words, 2 to 4 short paragraphs.
- If there's a hook, open with it naturally and connect it to one pain in the next sentence. Mention
  the post only once.
- If there's no hook, open with a relevant observation about their role or company. Don't invent facts.
- One clear, low-friction call to action.
- Subject line: 2 to 6 words, lowercase, no clickbait.

Return JSON only:
{ "subject": "...", "body": "...", "confidence": <0-100> }"""

SCORE_SYSTEM = """You score how safe and worthwhile a cold email is to send without a person checking
it first. You are strict: when unsure, score lower so a person reviews it."""

SCORE_USER = """ICP: {{icp.name}} ({{icp.titles}} in {{icp.industry}})
Pains: {{icp.pains}}
Offer: {{icp.offer}}
Proof we can claim: {{icp.proof}}

Lead: {{lead.firstName}} {{lead.lastName}}, {{lead.title}} at {{lead.company}} ({{lead.website}})

Content found for this lead:
{{items}}

Hook used (may be empty): {{hook.text}}

Drafted email
Subject: {{email.subject}}
{{email.body}}

Score from 0 to 100:
- Fit, up to 40: does the lead match the ICP's titles and industries?
- Hook, up to 30: is the opening grounded in a real, recent, relevant item? With no hook, at most 10.
- Email, up to 30: does it read naturally, follow the rules, avoid invented facts and end on one clear call to action?
Subtract 30 for anything sensitive (grief, illness, politics, religion, family) or any claim the proof does not support.

Return JSON only:
{ "score": <0-100>, "reasons": ["<short reason>", "..."] }
Give 2 to 4 reasons, most important first."""

DEFAULTS: dict[str, DefaultPrompt] = {
    "verify": DefaultPrompt("Default company verification", VERIFY_SYSTEM, VERIFY_USER, 0, 4096),
    "hook": DefaultPrompt("Default hook selection", HOOK_SYSTEM, HOOK_USER, 0.2, 800),
    "email": DefaultPrompt("Default email generation", EMAIL_SYSTEM, EMAIL_USER, 0.7, 800),
    "score": DefaultPrompt("Default email scoring", SCORE_SYSTEM, SCORE_USER, 0, 400),
}


def render(template: str, variables: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: variables.get(m.group(1), ""), template)


def _lines(values: list[str] | None) -> str:
    return ", ".join(v for v in values or [] if v) or "(none)"


def verify_variables(
    icp: dict[str, Any], profile: dict[str, Any] | None, evidence: dict[str, Any]
) -> dict[str, str]:
    """The placeholders a Company Verification prompt can use, from the ICP and a company's evidence."""
    profile = profile or {}
    rules = [
        f"- Words that identify them: {_lines(icp.get('any_of'))}",
        f"- On their website: {_lines(icp.get('website_any_of'))}",
        f"- On their Instagram: {_lines(icp.get('instagram_any_of'))}",
        f"- Never these: {_lines(icp.get('none_of'))}",
        f"- Searched as: {_lines(icp.get('search_terms'))}",
        f"- Places: {_lines(icp.get('places'))}",
    ]
    return {
        "icp.name": icp.get("name") or "",
        "icp.industry": icp.get("industry") or "",
        "icp.places": _lines(icp.get("places")),
        "icp.description": profile.get("description") or "",
        "icp.pains": profile.get("pains") or "",
        "icp.offer": profile.get("offer") or "",
        "icp.rules": "\n".join(rules),
        **company_variables(evidence),
    }


def _website(evidence: dict[str, Any]) -> str:
    """Each page read on its own, labelled with its address; else the combined text."""
    pages = [p for p in evidence.get("pages") or [] if (p.get("text") or "").strip()]
    if pages:
        return "\n\n".join(
            f'<page number="{i}" url="{p["url"]}">\n{" ".join(p["text"].split())[:PAGE_CHARS]}\n</page>'
            for i, p in enumerate(pages, 1)
        )
    return evidence.get("website_text") or "(couldn't be read)"


def company_variables(evidence: dict[str, Any]) -> dict[str, str]:
    """The {{company.*}} placeholders, for any prompt kind, from what we know about a company.

    evidence: name, domain, location, socials {network: url}, pages [{url, text}] or website_text,
    instagram (profile dict), apollo (company facts), summary (Company Verification's verdict).
    """
    ig = evidence.get("instagram")
    return {
        "company.name": evidence.get("name") or "",
        "company.domain": evidence.get("domain") or "(no website)",
        "company.location": evidence.get("location") or "(unknown)",
        "company.socials": ", ".join(f"{k}: {v}" for k, v in (evidence.get("socials") or {}).items()) or "(none found)",
        "company.apollo": json.dumps(evidence.get("apollo") or {}, indent=2, ensure_ascii=False),
        "company.instagram": json.dumps(ig, indent=2, ensure_ascii=False) if ig else "(not found)",
        "company.website": _website(evidence),
        "company.summary": evidence.get("summary") or "",
    }
