"""Writing an email for a lead with Claude, in two steps, each on the ICP's chosen prompt.

  1. Hook selection: the person's recent posts and their company's own posts, numbered, go to the
     Hook Selection prompt. Claude lists the main points worth mentioning, ranks them, and picks one
     to focus on (or none). Skipped when there's nothing to read.
  2. Email generation: the ICP, the person and the chosen hook go to the Email Generation prompt,
     which writes the subject and body.

Answers come back in a fixed JSON shape (structured outputs), whatever the prompt asks for. Nothing
here sends an email: the result is a draft for a person to review.

Temperature is never sent (Claude Haiku 5.5 rejects it). Thinking is on by default and counts toward
max tokens, so a prompt's max tokens is raised to at least MIN_MAX_TOKENS.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import anthropic
from pydantic import BaseModel

from services.claude import error_message
from services.prompts import render

MIN_MAX_TOKENS = 4096
DEFAULT_POSTS = 15  # when a prompt version doesn't set max_posts
ITEM_CHARS = 700
SENSITIVE_FLAGS = {"sensitive_topic"}
EXAMPLE_CHARS = 1500


class HookPoint(BaseModel):
    item: int | None
    point: str
    why: str
    relevance: int


class HookChoice(BaseModel):
    main_points: list[HookPoint]
    chosen_item: int | None
    focus: str
    hook: str
    relevance: int
    flags: list[str]


class EmailDraft(BaseModel):
    subject: str
    body: str
    confidence: int


@dataclass(frozen=True)
class PromptVersionSpec:
    version_id: UUID | None
    system: str
    user: str
    model: str
    max_tokens: int
    max_posts: int | None = None

    @property
    def posts(self) -> int:
        return self.max_posts or DEFAULT_POSTS


@dataclass
class Item:
    """One numbered piece of content Claude may build the email around."""

    post_id: UUID
    whose: str  # person | company
    source: str  # linkedin | instagram
    posted_at: datetime | None
    text: str
    url: str


@dataclass
class Example:
    """An earlier email for the same ICP that a person rated, shown to Claude in the email step."""

    rating: str  # good | bad
    subject: str
    body: str
    hook: str
    tags: list[str]
    note: str


@dataclass
class LeadContext:
    icp: dict[str, Any]
    profile: dict[str, Any] | None
    lead: dict[str, Any]  # first_name, last_name, title, company, website, extra
    items: list[Item]
    instruction: str = ""
    examples: list[Example] = field(default_factory=list)


@dataclass
class Call:
    step: str  # hook | email
    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class Draft:
    hook: HookChoice | None = None
    chosen: Item | None = None
    email: EmailDraft | None = None
    error: str | None = None
    calls: list[Call] = field(default_factory=list)


def format_items(items: list[Item]) -> str:
    if not items:
        return "(no posts found)"
    lines = []
    for i, it in enumerate(items, 1):
        date = it.posted_at.date().isoformat() if it.posted_at else "undated"
        who = "their own post" if it.whose == "person" else "their company's post"
        text = " ".join(it.text.split())[:ITEM_CHARS] or "(no text)"
        lines.append(f"{i}. [{it.source}, {date}, {who}] {text}")
    return "\n".join(lines)


def format_examples(examples: list[Example]) -> str:
    """Rated emails as a block for the email step: write like the good ones, avoid what the bad ones did."""
    if not examples:
        return ""
    good = [e for e in examples if e.rating == "good"]
    bad = [e for e in examples if e.rating == "bad"]

    def one(e: Example) -> str:
        why = ", ".join(t.replace("_", " ") for t in e.tags)
        lines = [f"Subject: {e.subject}", e.body.strip()[:EXAMPLE_CHARS]]
        if why or e.note.strip():
            lines.append(f"Reviewer said: {'; '.join(x for x in (why, e.note.strip()) if x)}")
        return "\n".join(lines)

    parts = ["Feedback from the person reviewing these emails, on earlier emails for this same ICP."]
    if good:
        parts.append("Emails they rated GOOD. Match their style, length and approach (not their facts):")
        parts += [f"<good_example>\n{one(e)}\n</good_example>" for e in good]
    if bad:
        parts.append("Emails they rated BAD. Avoid what they were criticised for:")
        parts += [f"<bad_example>\n{one(e)}\n</bad_example>" for e in bad]
    parts.append("These are about other people and companies. Never reuse their details in this email.")
    return "\n\n".join(parts)


def variables(
    ctx: LeadContext, items: list[Item], hook: HookChoice | None = None, chosen: Item | None = None
) -> dict[str, str]:
    """The same placeholder names the prompt editor offers (lib/templating.ts). `items` is {{items}}:
    the newest posts, as many as the prompt being filled allows."""
    icp, profile, lead = ctx.icp, ctx.profile or {}, ctx.lead
    out = {
        "icp.name": icp.get("name") or "",
        "icp.industry": icp.get("industry") or "",
        "icp.places": ", ".join(icp.get("places") or []),
        "icp.titles": ", ".join(profile.get("titles") or []),
        "icp.description": profile.get("description") or "",
        "icp.pains": profile.get("pains") or "",
        "icp.offer": profile.get("offer") or "",
        "icp.proof": profile.get("proof") or "",
        "icp.cta": profile.get("cta") or "",
        "icp.tone": profile.get("tone") or "",
        "icp.exampleEmail": profile.get("example_email") or "",
        "icp.wordsToAvoid": profile.get("words_to_avoid") or "",
        "icp.hookGuidance": profile.get("hook_guidance") or "",
        "lead.firstName": lead.get("first_name") or "",
        "lead.lastName": lead.get("last_name") or "",
        "lead.title": lead.get("title") or "",
        "lead.company": lead.get("company") or "",
        "lead.website": lead.get("website") or "",
        "items": format_items(items),
        "hook.text": hook.hook if hook else "",
        "hook.summary": hook.focus if hook else "",
        "hook.post": " ".join(chosen.text.split())[:ITEM_CHARS * 3] if chosen else "",
        "hook.source": chosen.source if chosen else "",
        "hook.url": chosen.url if chosen else "",
        "hook.date": chosen.posted_at.date().isoformat() if chosen and chosen.posted_at else "",
    }
    for key, value in (lead.get("extra") or {}).items():
        out[f"lead.extra.{key}"] = str(value)
    return out


class EmailWriter:
    def __init__(self, api_key: str, client: anthropic.AsyncAnthropic | None = None, concurrency: int = 3) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=4, timeout=180.0)
        self._gate = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._client.close()

    async def _ask(self, spec: PromptVersionSpec, user: str, effort: str, shape: type[BaseModel], step: str, draft: Draft):
        response = await self._client.messages.parse(
            model=spec.model,
            max_tokens=max(spec.max_tokens, MIN_MAX_TOKENS),
            system=spec.system,
            output_config={"effort": effort},
            output_format=shape,
            messages=[{"role": "user", "content": user}],
        )
        draft.calls.append(Call(step, spec.model, response.usage.input_tokens, response.usage.output_tokens))
        if response.stop_reason == "refusal":
            raise _Stop(f"Claude declined the {step} step.")
        if response.stop_reason == "max_tokens":
            raise _Stop(f"Claude ran out of room in the {step} step; raise that prompt's max tokens.")
        if response.parsed_output is None:
            raise _Stop(f"Claude's {step} answer couldn't be read.")
        return response.parsed_output

    async def write(self, ctx: LeadContext, hook_spec: PromptVersionSpec, email_spec: PromptVersionSpec) -> Draft:
        draft = Draft()
        async with self._gate:
            try:
                # Posts are newest first; each prompt gets as many as it allows.
                hook_items = ctx.items[: hook_spec.posts]
                if hook_items:
                    hook: HookChoice = await self._ask(
                        hook_spec, render(hook_spec.user, variables(ctx, hook_items)), "low", HookChoice, "hook", draft
                    )
                    hook.relevance = max(0, min(5, hook.relevance))
                    for p in hook.main_points:
                        p.relevance = max(0, min(5, p.relevance))
                    if hook.chosen_item is not None and 1 <= hook.chosen_item <= len(hook_items) and hook.hook.strip():
                        draft.chosen = hook_items[hook.chosen_item - 1]
                    else:
                        hook.chosen_item, hook.hook, hook.focus = None, "", ""
                    draft.hook = hook
                user = render(email_spec.user, variables(ctx, ctx.items[: email_spec.posts], draft.hook, draft.chosen))
                if ctx.examples:
                    user += "\n\n" + format_examples(ctx.examples)
                if ctx.instruction.strip():
                    user += f"\n\nAn extra instruction from the person reviewing this email: {ctx.instruction.strip()}"
                email: EmailDraft = await self._ask(email_spec, user, "medium", EmailDraft, "email", draft)
                email.subject, email.body = email.subject.strip(), email.body.strip()
                email.confidence = max(0, min(100, email.confidence))
                draft.email = email
            except _Stop as stop:
                draft.error = str(stop)
            except anthropic.APIError as exc:
                draft.error = error_message(exc)
        return draft


class _Stop(Exception):
    pass
