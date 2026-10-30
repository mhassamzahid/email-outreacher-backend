"""The sending mailboxes, as CheapSequencer shows them, with their domains' health from CheapInboxes.

Read-only: one GET gathers the mailboxes (limits, warmup, today's sends and placements), plan usage,
the campaigns and each domain's DNS health. Either service can be missing or down; the page then
shows what the other one returned and says why the rest is missing.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from dependencies import get_optional_inboxes, get_optional_sequencer
from schemas import CampaignBrief, DomainHealth, MailboxOut, MailboxesOut, SendingSummary
from services.sending import InboxesClient, SendingError, SequencerClient

router = APIRouter(tags=["mailboxes"])


# CheapSequencer finds replies by syncing each mailbox's inbox. Older than this, replies there are missed.
SYNC_STALE_HOURS = 2


def _sync_age_hours(synced_at: str | None, now: datetime) -> float | None:
    if not synced_at:
        return None
    try:
        return (now - datetime.fromisoformat(synced_at.replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        return None


def _mailbox(row: dict[str, Any], synced_at: str | None, now: datetime) -> MailboxOut:
    limits = row.get("send_limits") or {}
    warm = row.get("warmup") or {}
    today = row.get("today") or {}
    error = (row.get("health") or {}).get("last_error_message")
    age = _sync_age_hours(synced_at, now)
    stale = age is None or age > SYNC_STALE_HOURS
    if stale and not error:
        error = ("CheapSequencer has never read this inbox" if age is None else
                 f"CheapSequencer last read this inbox {age:.0f} hours ago") + ", so replies here aren't being picked up."  # fmt: skip
    status, auth = row.get("status"), row.get("auth_status")
    return MailboxOut(
        id=row["id"], email=row.get("email") or "", name=row.get("display_name") or "",
        domain=(row.get("email") or "").split("@")[-1], provider=row.get("provider") or "",
        status=status or "unknown", auth_status=auth or "unknown",
        needs_attention=bool(error) or status != "connected" or auth not in (None, "ok"),
        last_error=error, tags=row.get("tags") or [], last_synced_at=synced_at, sync_stale=stale,
        campaign_daily=limits.get("sequence_daily") or 0, warmup_daily=limits.get("warmup_daily") or 0,
        total_daily=limits.get("total_daily") or 0,
        warmup_enabled=bool(warm.get("enabled")), warmup_state=warm.get("state"),
        warmup_started_at=warm.get("started_at"), last_sent_at=warm.get("last_sent_at"),
        today_sends=today.get("sends") or 0, today_replies=today.get("replies") or 0, today_failed=today.get("failed") or 0,
        today_inbox=today.get("inbox_placements") or 0, today_spam=today.get("spam_placements") or 0,
        today_saved=today.get("saved_from_spam") or 0, today_unknown=today.get("unknown_placements") or 0,
    )  # fmt: skip


async def _domains(inboxes: InboxesClient) -> list[DomainHealth]:
    rows = await inboxes.domains()

    async def health(row: dict[str, Any]) -> DomainHealth:
        try:
            h = await inboxes.dns_health(row["id"])
        except SendingError as exc:
            return DomainHealth(domain=row.get("domain") or "", status=row.get("status") or "", health="unknown", problems=[exc.message])
        failing = [f"{c.get('title') or c.get('id')}: {c.get('summary')}" for c in h.get("checks") or []
                   if c.get("state") not in ("pass", "not_applicable")]  # fmt: skip
        return DomainHealth(domain=row.get("domain") or "", status=row.get("status") or "", health=h.get("overall") or "unknown", problems=failing)

    return sorted(await asyncio.gather(*(health(r) for r in rows)), key=lambda d: d.domain)


@router.get("/mailboxes", response_model=MailboxesOut)
async def mailboxes(
    sequencer: SequencerClient | None = Depends(get_optional_sequencer),
    inboxes: InboxesClient | None = Depends(get_optional_inboxes),
) -> MailboxesOut:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    synced: dict[str, str | None] = {}
    usage: dict[str, Any] = {}
    warmup: dict[str, Any] = {}
    campaigns: list[dict[str, Any]] = []
    domains: list[DomainHealth] = []

    if sequencer is None:
        errors.append("CHEAPSEQUENCER_API_KEY is not set, so the mailboxes can't be read.")
    else:
        try:
            rows, usage, warmup, campaigns, inbox_rows = await asyncio.gather(
                sequencer.warmup_inboxes(), sequencer.usage(), sequencer.warmup(), sequencer.campaigns(), sequencer.inboxes()
            )
            synced = {r["id"]: (r.get("sync") or {}).get("last_synced_at") for r in inbox_rows if r.get("id")}
        except SendingError as exc:
            errors.append(exc.message)
    if inboxes is None:
        errors.append("CHEAPINBOXES_API_KEY is not set, so domain health can't be read.")
    else:
        try:
            domains = await _domains(inboxes)
        except SendingError as exc:
            errors.append(exc.message)

    sends = usage.get("sends") or {}
    connected = usage.get("connected_inboxes") or {}
    today = (warmup.get("metrics") or {})
    week = (warmup.get("performance") or {})
    summary = SendingSummary(
        connected=connected.get("used") or len(rows), connected_limit=connected.get("limit"),
        sends_used=sends.get("used") or 0, sends_limit=sends.get("limit"),
        period_ends_at=(usage.get("billing_period") or {}).get("ends_at"),
        campaign_daily=sum(r.get("send_limits", {}).get("sequence_daily") or 0 for r in rows),
        warmup_today_sends=today.get("sends_today") or 0, warmup_today_inbox=today.get("inbox_placements_today") or 0,
        warmup_today_spam=today.get("spam_placements_today") or 0, warmup_today_saved=today.get("saved_from_spam_today") or 0,
        warmup_week_sent=week.get("sent_count") or 0, warmup_week_inbox=week.get("inbox_count") or 0,
        warmup_week_spam=week.get("spam_count") or 0, warmup_week_saved=week.get("saved_from_spam_count") or 0,
        needing_review=today.get("inboxes_needing_review") or 0,
    )  # fmt: skip
    now = datetime.now(UTC)
    boxes = sorted((_mailbox(r, synced.get(r["id"]), now) for r in rows), key=lambda m: (m.domain, m.email))
    if stale := [m for m in boxes if m.sync_stale]:
        errors.append(f"CheapSequencer hasn't read the inbox of {len(stale)} of {len(boxes)} mailboxes in over {SYNC_STALE_HOURS} hours, "
                      "so replies there aren't being detected and those leads can still get follow-ups. Contact CheapSequencer support.")  # fmt: skip
    return MailboxesOut(
        summary=summary,
        mailboxes=boxes,
        domains=domains,
        campaigns=[CampaignBrief(name=c.get("name") or "", status=c.get("status") or "", daily_send_cap=c.get("daily_send_cap")) for c in campaigns],
        errors=errors,
    )
