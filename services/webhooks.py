"""CheapSequencer's webhooks: proving a delivery came from CheapSequencer, and what each event means here.

CheapSequencer signs every delivery (https://docs.cheapsequencer.com/api/v1/webhooks/):

  X-CheapSequencer-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with the secret>

The secret is shown once, when the subscription is made (scripts/subscribe_webhook.py), and lives in
CHEAPSEQUENCER_WEBHOOK_SECRET. Deliveries come at least once, so each event is stored once, by its id.

Three events matter here: a reply, a bounce, and an enrollment stopping (an unsubscribe, say). Each
stops the lead's sequence at once; the follow-up check would otherwise see it up to 30 minutes later,
and could write a follow-up for someone who already answered.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# The events the subscription asks for.
EVENT_TYPES = ["message.replied", "message.bounced", "enrollment.stopped"]
# A delivery signed longer ago than this is refused, so a captured one can't be replayed later.
TOLERANCE_SECONDS = 300


def verify(secret: str, header: str | None, body: bytes, now: float) -> bool:
    """Whether the signature header is CheapSequencer's, for this exact body, signed recently."""
    if not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        signed_at = int(parts["t"][0])
    except (KeyError, ValueError):
        return False
    if abs(now - signed_at) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{signed_at}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, given) for given in parts.get("v1", []))


@dataclass
class Stop:
    """What an event says happened to a lead in a campaign."""

    event_id: str
    type: str  # replied | bounced | unsubscribed (eo_send_events.type)
    lead_id: str | None  # CheapSequencer's lead id
    campaign_id: str | None
    occurred_at: datetime


def _reason(value: Any) -> str | None:
    reason = str(value or "").lower()
    if reason in ("replied", "reply", "lead_replied"):
        return "replied"
    if "bounce" in reason:
        return "bounced"
    if "unsubscribe" in reason:
        return "unsubscribed"
    return None  # a manual stop, a removal, a colleague's reply...: the follow-up check records those


def stop_of(event: dict[str, Any]) -> Stop | None:
    """The stop an event reports, or None for anything else (unknown events are expected, and ignored)."""
    data = event.get("data") or {}
    kind = {"message.replied": "replied", "message.bounced": "bounced"}.get(event.get("type") or "")
    if event.get("type") == "enrollment.stopped":
        kind = _reason(data.get("reason"))
    if kind is None or not event.get("id"):
        return None
    when = data.get("transitioned_at") or event.get("occurred_at")
    try:
        occurred_at = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return None
    campaign = data.get("sequence_id") or data.get("campaign_id")
    return Stop(str(event["id"]), kind, data.get("lead_id"), campaign, occurred_at)
