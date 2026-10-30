"""Coldbrew's emails, sent through CheapSequencer's campaigns. Nothing here activates a campaign or
sends: a campaign only sends once a person activates it in CheapSequencer.

  queue       An approved first email goes into its ICP's campaign ("Coldbrew: <ICP>"): the lead is
              imported into a new list with email_1_subject and email_1_body, and the generic
              follow-ups in email_2_body and email_3_body (so a step can never go out blank), then
              the list is enrolled.
  follow-ups  Every run reads each campaign's enrollments. When a lead's next email is due within
              the lead time (last send + the step's delay) and they haven't replied, bounced or
              unsubscribed, Claude writes it with the follow-up prompt and it replaces the generic
              text. Nobody who already replied costs a follow-up.
  templates   The campaigns' step text: each lead's email, then the sender's name, company and
              physical address from Settings (US law asks for an address in commercial email).
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from crud import PRODUCT_FIELDS, exclusion_note, person_keys
from models import Email, Lead
from services.claude import cost_usd
from services.generation import PromptVersionSpec, Sample, format_samples, write_follow_up
from services.prompts import product_variables, render
from services.sending import SendingError

log = logging.getLogger("coldbrew.outreach")

CAMPAIGN_PREFIX = "Coldbrew: "
DEFAULT_DELAYS = {2: 3, 3: 5}  # days after the previous email, as the campaigns are set up
ENDED = {"completed", "stopped", "paused", "removed", "cancelled"}


# ---------- Pure helpers ----------


def first_name_in(text: str, first_name: str) -> str:
    """The generic follow-ups say {{first_name}}; CheapSequencer may not resolve tags inside a field's
    value, so the name is filled in here. Without a name, 'Hi there'."""
    return re.sub(r"\{\{\s*first_name\s*\}\}", first_name.strip() or "there", text)


def signature(company: str, address: str) -> str:
    """What follows every email: the sender's name, then the company and its address."""
    lines = ["{{sender_name}}", company.strip(), *[ln.strip() for ln in address.strip().splitlines() if ln.strip()]]
    return "\n".join(ln for ln in lines if ln)


def step_body(step: int, company: str, address: str) -> str:
    return f"{{{{email_{step}_body}}}}\n\n{signature(company, address)}"


def lead_row(lead: Lead, subject: str, body: str, fallback_2: str, fallback_3: str) -> dict[str, str]:
    """One CSV row for CheapSequencer: built-in lead fields plus the four email fields."""
    first = lead.first_name or ""
    return {
        "email": (lead.email or "").strip().lower(),
        "first_name": first,
        "last_name": lead.last_name or "",
        "company_name": lead.company or "",
        "job_title": lead.title or "",
        "website": lead.website or "",
        "linkedin_profile": lead.linkedin_url or "",
        "email_1_subject": subject,
        "email_1_body": body,
        "email_2_body": first_name_in(fallback_2, first),
        "email_3_body": first_name_in(fallback_3, first),
    }


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Progress:
    status: str
    sent: int
    last_sent_at: datetime | None
    stop_reason: str | None


def progress(enrollment: dict[str, Any]) -> Progress:
    """Where a lead stands, from a CheapSequencer enrollment."""
    reason = next((name for key, name in (("replied_at", "replied"), ("bounced_at", "bounced"), ("unsubscribed_at", "unsubscribed"))
                   if enrollment.get(key)), None)  # fmt: skip
    if not reason and enrollment.get("failure"):
        reason = "failed"
    return Progress(
        status=str(enrollment.get("status") or "unknown"),
        sent=int(enrollment.get("sent_count") or 0),
        last_sent_at=_when(enrollment.get("last_sent_at")),
        stop_reason=reason,
    )


def due_follow_up(p: Progress, delays: dict[int, int], now: datetime, lead_hours: int) -> int | None:
    """The follow-up (2 or 3) to write now: the lead is still active with no reply, the previous email
    has gone out, and the next one is due within `lead_hours`."""
    if p.status != "active" or p.stop_reason or p.last_sent_at is None:
        return None
    step = p.sent + 1
    if step not in (2, 3):
        return None
    due = p.last_sent_at + timedelta(days=delays.get(step, DEFAULT_DELAYS[step]))
    return step if now >= due - timedelta(hours=lead_hours) else None


def delays_of(campaign: dict[str, Any]) -> dict[int, int]:
    steps = campaign.get("steps") or []
    out = dict(DEFAULT_DELAYS)
    for index, s in enumerate(steps[:3], start=1):
        if index > 1 and isinstance(s.get("delay_days"), int):
            out[index] = s["delay_days"]
    return out


def key_for(*parts: Any) -> str:
    """A stable idempotency key: retrying the same work never repeats it in CheapSequencer."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]
    return f"coldbrew-{parts[0]}-{digest}"


# ---------- Wiring ----------


@dataclass
class Deps:
    workspace_id: UUID
    sending: Any  # SendingRepo
    settings: Any  # SettingsRepo
    profiles: Any  # IcpProfileRepo
    prompts: Any  # PromptRepo
    products: Any  # ProductRepo
    library: Any  # ExampleRepo
    exclusions: Any  # ExclusionRepo
    calls: Any  # ProviderCallRepo
    lead_intel: Any  # LeadIntelRepo
    sequencer: Any  # SequencerClient
    writer: Any = None  # EmailWriter, for follow-ups


async def campaign_for_icps(deps: Deps, icp_ids: set[UUID]) -> dict[UUID, tuple[str, str]]:
    """Each ICP's campaign (id, name): the one chosen on its profile, else "Coldbrew: <ICP name>"."""
    campaigns = await deps.sequencer.campaigns()
    by_name = {c.get("name"): c for c in campaigns}
    by_id = {c.get("id"): c for c in campaigns}
    profiles = await deps.profiles.get_many(deps.workspace_id, list(icp_ids))
    out: dict[UUID, tuple[str, str]] = {}
    for icp_id in icp_ids:
        chosen = by_id.get(getattr(profiles.get(icp_id), "sequencer_campaign_id", None))
        if chosen is None:
            icp = await deps.lead_intel.get_icp(icp_id)
            chosen = by_name.get(f"{CAMPAIGN_PREFIX}{icp['name']}") if icp else None
        if chosen:
            out[icp_id] = (chosen["id"], chosen.get("name") or "")
    return out


@dataclass
class QueueOutcome:
    email_id: UUID
    status: str  # queued | skipped
    reason: str | None = None
    campaign: str | None = None


async def queue(deps: Deps, email_ids: list[UUID]) -> list[QueueOutcome]:
    """Put approved first emails into their ICP's campaign. The campaign isn't activated."""
    settings = await deps.settings.sending(deps.workspace_id)
    if not settings["sender_company"].strip() or not settings["sender_address"].strip():
        raise SendingError("Add the sender's company and physical address in Settings, under Sending, before queueing emails.")
    pairs = await deps.sending.to_queue(deps.workspace_id, email_ids)
    found = {e.id for e, _ in pairs}
    outcomes = [QueueOutcome(i, "skipped", "Not an approved first email, or already queued") for i in email_ids if i not in found]
    is_excluded = await deps.exclusions.matcher()

    groups: dict[UUID, list[tuple[Email, Lead]]] = {}
    for email, lead in pairs:
        if not lead.email:
            outcomes.append(QueueOutcome(email.id, "skipped", "The lead has no email address"))
        elif hit := is_excluded(person_keys(lead.email, lead.domain)):
            outcomes.append(QueueOutcome(email.id, "skipped", exclusion_note(hit)))
        elif not lead.lead_intel_icp_id:
            outcomes.append(QueueOutcome(email.id, "skipped", "The lead has no ICP, so there's no campaign to put it in"))
        else:
            groups.setdefault(lead.lead_intel_icp_id, []).append((email, lead))
    if not groups:
        return outcomes

    campaigns = await campaign_for_icps(deps, set(groups))
    for icp_id, members in groups.items():
        if icp_id not in campaigns:
            icp = await deps.lead_intel.get_icp(icp_id)
            name = icp["name"] if icp else "this ICP"
            outcomes += [QueueOutcome(e.id, "skipped", f'No CheapSequencer campaign named "{CAMPAIGN_PREFIX}{name}"') for e, _ in members]
            continue
        campaign_id, campaign_name = campaigns[icp_id]
        ids = sorted(str(e.id) for e, _ in members)
        rows = [lead_row(lead, e.subject, e.edited_body or e.body, settings["followup_fallback_2"], settings["followup_fallback_3"])
                for e, lead in members]  # fmt: skip
        try:
            list_id = await deps.sequencer.create_list(f"{campaign_name} {datetime.now(UTC):%Y-%m-%d %H:%M}", key_for("list", *ids))
            await deps.sequencer.import_rows(list_id, rows, key_for("import", *ids))
            await deps.sequencer.enroll(campaign_id, list_id, key_for("enroll", campaign_id, *ids))
        except SendingError as exc:
            outcomes += [QueueOutcome(e.id, "skipped", exc.message) for e, _ in members]
            continue
        await deps.sending.mark_queued([e for e, _ in members], campaign_id, list_id)
        outcomes += [QueueOutcome(e.id, "queued", None, campaign_name) for e, _ in members]
    return outcomes


@dataclass
class TemplateOutcome:
    campaign: str
    status: str  # updated | unchanged | skipped
    reason: str | None = None


async def sync_templates(deps: Deps) -> list[TemplateOutcome]:
    """Write the signature (sender name, company, address) into every Coldbrew campaign's steps.
    Only draft or paused campaigns can be changed; an active one is reported, not touched."""
    settings = await deps.settings.sending(deps.workspace_id)
    company, address = settings["sender_company"], settings["sender_address"]
    out: list[TemplateOutcome] = []
    for summary in await deps.sequencer.campaigns():
        name = summary.get("name") or ""
        if not name.startswith(CAMPAIGN_PREFIX):
            continue
        campaign, etag = await deps.sequencer.campaign(summary["id"])
        if campaign.get("status") not in ("draft", "paused"):
            out.append(TemplateOutcome(name, "skipped", "It's sending. Pause it in CheapSequencer to change its text."))
            continue
        steps = campaign.get("steps") or []
        wanted = [step_body(i, company, address) for i in range(1, len(steps) + 1)]
        if [s.get("body_text") for s in steps] == wanted:
            out.append(TemplateOutcome(name, "unchanged"))
            continue
        new_steps = []
        for i, s in enumerate(steps, start=1):
            step = {k: s[k] for k in ("id", "delay_days", "thread_mode", "send_as_plain_text") if k in s}
            step["body_text"] = wanted[i - 1]
            if i == 1:
                step["subject"] = s.get("subject") or "{{email_1_subject}}"
            new_steps.append(step)
        try:
            await deps.sequencer.set_steps(summary["id"], new_steps, etag, key_for("steps", summary["id"], *wanted))
            out.append(TemplateOutcome(name, "updated"))
        except SendingError as exc:
            out.append(TemplateOutcome(name, "skipped", exc.message))
    return out


@dataclass
class FollowUpRun:
    checked: int = 0
    written: int = 0
    ended: int = 0
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)


async def _variables(deps: Deps, email: Email, lead: Lead, step: int, earlier: dict[int, str]) -> dict[str, str]:
    icp_id = lead.lead_intel_icp_id
    icp = (await deps.lead_intel.get_icp(icp_id)) if icp_id else None
    profile = (await deps.profiles.get_many(deps.workspace_id, [icp_id])).get(icp_id) if icp_id else None
    product = await deps.products.get(deps.workspace_id, profile.product_id) if profile and profile.product_id else None
    samples = [Sample(s.subject, s.body, s.note) for s in await deps.library.for_icp(deps.workspace_id, icp_id, 3)] if icp_id else []
    hooks = await deps.sending.hooks(lead.id)
    first, last = lead.first_name or "", lead.last_name or ""
    out = {
        "lead.firstName": first, "lead.lastName": last, "lead.fullName": f"{first} {last}".strip(),
        "lead.title": lead.title or "", "lead.company": lead.company or "", "lead.website": lead.website or "",
        "icp.name": (icp or {}).get("name") or "", "icp.pains": getattr(profile, "pains", "") or "",
        "icp.offer": getattr(profile, "offer", "") or "", "icp.proof": getattr(profile, "proof", "") or "",
        "icp.cta": getattr(profile, "cta", "") or "", "icp.tone": getattr(profile, "tone", "") or "",
        "icp.wordsToAvoid": getattr(profile, "words_to_avoid", "") or "",
        "hooks": "\n".join(f"{h.rank}. {h.hook or h.point} ({h.source})" for h in hooks) or "(none)",
        "hook.text": email.hook or "",
        "examples.samples": format_samples(samples),
        "email1.subject": email.subject, "email1.body": email.edited_body or email.body,
        "email2.body": earlier.get(2, ""), "followup.step": str(step),
    }  # fmt: skip
    out.update(product_variables(None if product is None else {k: getattr(product, k) for k in PRODUCT_FIELDS}))
    return out


async def run_follow_ups(deps: Deps, now: datetime | None = None) -> FollowUpRun:
    """Read every campaign's enrollments, record where each lead stands, and write the follow-ups that
    are nearly due for leads who haven't replied."""
    now = now or datetime.now(UTC)
    run = FollowUpRun()
    pairs = await deps.sending.in_sequence(deps.workspace_id)
    if not pairs:
        return run
    settings = await deps.settings.sending(deps.workspace_id)
    lead_hours = settings["followup_lead_hours"]

    enrollments: dict[str, dict[str, dict[str, Any]]] = {}
    delays: dict[str, dict[int, int]] = {}
    for campaign_id in {e.sequencer_campaign_id for e, _ in pairs}:
        try:
            campaign, _ = await deps.sequencer.campaign(campaign_id)
            rows = await deps.sequencer.enrollments(campaign_id)
        except SendingError as exc:
            run.notes.append(exc.message)
            continue
        delays[campaign_id] = delays_of(campaign)
        enrollments[campaign_id] = {str(((r.get("lead") or {}).get("email") or "")).lower(): r for r in rows}

    existing = await deps.sending.follow_ups([lead.id for _, lead in pairs])
    to_write: dict[str, list[tuple[Email, Lead, int, str, Any]]] = {}
    for email, lead in pairs:
        row = enrollments.get(email.sequencer_campaign_id, {}).get((lead.email or "").lower())
        if row is None:
            continue
        run.checked += 1
        p = progress(row)
        email.sequencer_lead_id = (row.get("lead") or {}).get("id") or email.sequencer_lead_id
        email.sequence_status, email.sequence_sent_count = p.status, p.sent
        email.sequence_last_sent_at, email.sequence_stop_reason = p.last_sent_at, p.stop_reason
        email.sequence_synced_at, email.sequence_raw = now, row
        if p.sent >= 1 and email.status == "queued":
            email.status, email.sent_at = "sent", email.sent_at or p.last_sent_at
        for step in (2, 3):
            later = existing.get((lead.id, step))
            if later is not None and p.sent >= step and later.status == "queued":
                later.status, later.sent_at = "sent", p.last_sent_at
        if p.status in ENDED or p.stop_reason:
            run.ended += 1
            continue
        step = due_follow_up(p, delays.get(email.sequencer_campaign_id, DEFAULT_DELAYS), now, lead_hours)
        if step is None or (lead.id, step) in existing:
            continue
        if deps.writer is None:
            run.notes.append("ANTHROPIC_API_KEY is not set, so follow-ups keep their generic text.")
            break
        version = await deps.prompts.for_icp(deps.workspace_id, f"followup{step}", None)
        if version is None:
            continue
        spec = PromptVersionSpec(version.id, version.system_prompt, version.user_prompt, version.model, version.max_tokens)
        earlier = {k: (existing[(lead.id, k)].edited_body or existing[(lead.id, k)].body) for k in (2,) if (lead.id, k) in existing}
        variables = await _variables(deps, email, lead, step, earlier)
        draft, calls, error = await write_follow_up(deps.writer, spec, render(spec.user, variables))
        cost = sum(cost_usd(c.model, c.input_tokens, c.output_tokens) for c in calls)
        if calls:
            await deps.calls.record(
                deps.workspace_id, "claude", "messages", "failed" if error else "succeeded", cost_usd=round(cost, 4),
                action="Write follow-ups", model=calls[-1].model, lead_id=lead.id,
                input_tokens=sum(c.input_tokens for c in calls), output_tokens=sum(c.output_tokens for c in calls),
            )  # fmt: skip
        run.cost_usd += cost
        if error or draft is None:
            run.notes.append(f"{lead.email}: {error} (it keeps the generic follow-up until the next run)")
            continue
        to_write.setdefault(email.sequencer_list_id, []).append((email, lead, step, draft.body, (version.id, draft.confidence, cost)))
    await deps.sending.commit()

    for list_id, items in to_write.items():
        rows = [{"email": (lead.email or "").lower(), f"email_{step}_body": body} for _, lead, step, body, _ in items]
        try:
            await deps.sequencer.import_rows(list_id, rows, key_for("followup", list_id, *[f"{l.id}:{s}" for _, l, s, _, _ in items]))
        except SendingError as exc:
            run.notes.append(f"Follow-ups couldn't be written to CheapSequencer: {exc.message}")
            continue
        for email, _lead, step, body, (version_id, confidence, cost) in items:
            await deps.sending.add_follow_up(email, step, body, version_id, confidence, cost)
            run.written += 1
    return run


# ---------- Every 30 minutes ----------


async def follow_up_loop(build_deps, workspace_ids, every_seconds: int = 1800) -> None:
    """Run the follow-ups for every workspace with emails in a sequence, forever. One failing
    workspace never stops the others."""
    while True:
        try:
            for workspace_id in await workspace_ids():
                try:
                    async with build_deps(workspace_id) as deps:
                        result = await run_follow_ups(deps)
                    if result.written or result.notes:
                        log.info("follow-ups for %s: %s written, %s notes", workspace_id, result.written, result.notes)
                except Exception:  # noqa: BLE001
                    log.exception("follow-ups failed for workspace %s", workspace_id)
        except Exception:  # noqa: BLE001
            log.exception("follow-up loop failed")
        await asyncio.sleep(every_seconds)

