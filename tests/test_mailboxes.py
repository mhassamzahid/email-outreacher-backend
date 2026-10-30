"""The mailboxes page: CheapSequencer and CheapInboxes faked over HTTP. No network, no cost."""

import json

import httpx

from dependencies import get_optional_inboxes, get_optional_sequencer
from main import app
from services.sending import InboxesClient, SequencerClient

ROW = {
    "id": "i1", "email": "emily@meetaheadtech360.com", "display_name": "Emily Carter", "provider": "gmail",
    "tags": ["ci"], "status": "connected", "auth_status": "ok",
    "send_limits": {"total_daily": 48, "warmup_daily": 30, "sequence_daily": 18},
    "warmup": {"enabled": True, "state": "warming", "started_at": "2026-10-29T21:48:05Z", "last_sent_at": "2026-10-30T09:25:29Z"},
    "today": {"sends": 6, "replies": 0, "failed": 0, "inbox_placements": 5, "spam_placements": 1, "saved_from_spam": 1, "unknown_placements": 0},
    "health": {"last_error_message": None},
}  # fmt: skip


def sequencer(request: httpx.Request) -> httpx.Response:
    path, query = request.url.path, request.url.params
    if request.method != "GET":
        return httpx.Response(405, json={"error": {"message": "read-only test"}})
    if path.endswith("/warmup/inboxes"):
        # Two pages, to prove the cursor is followed.
        if query.get("cursor") == "c2":
            broken = ROW | {"id": "i2", "email": "james@meetaheadtech360.com", "display_name": "James Whitfield",
                            "auth_status": "reauth_required", "health": {"last_error_message": "Token expired"}}  # fmt: skip
            return httpx.Response(200, json={"data": [broken], "meta": {"has_more": False}})
        return httpx.Response(200, json={"data": [ROW], "meta": {"has_more": True, "next_cursor": "c2"}})
    if path.endswith("/usage"):
        return httpx.Response(200, json={"data": {"sends": {"used": 0, "limit": 10000}, "connected_inboxes": {"used": 2, "limit": 500},
                                                  "billing_period": {"ends_at": "2026-11-29T12:30:35Z"}}})  # fmt: skip
    if path.endswith("/warmup"):
        return httpx.Response(200, json={"data": {"metrics": {"sends_today": 12, "inbox_placements_today": 10, "spam_placements_today": 1},
                                                  "performance": {"sent_count": 74, "inbox_count": 54}}})  # fmt: skip
    if path.endswith("/campaigns"):
        return httpx.Response(200, json={"data": [{"name": "Coldbrew: DTF", "status": "draft", "daily_send_cap": 48}], "meta": {}})
    return httpx.Response(404, json={"error": {"message": "nope"}})


def inboxes(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/domains":
        return httpx.Response(200, json={"domains": [{"id": "d1", "domain": "meetaheadtech360.com", "status": "active"}]})
    if path.endswith("/dns-health"):
        return httpx.Response(200, json={"overall": "warning", "checks": [
            {"id": "spf", "title": "SPF", "state": "pass"}, {"id": "cname", "state": "not_applicable"},
            {"id": "dmarc", "title": "DMARC", "state": "fail", "summary": "No DMARC record."}]})  # fmt: skip
    return httpx.Response(404, json={"message": "nope"})


def test_mailboxes_page(client) -> None:
    app.dependency_overrides[get_optional_sequencer] = lambda: SequencerClient("k", transport=httpx.MockTransport(sequencer))
    app.dependency_overrides[get_optional_inboxes] = lambda: InboxesClient("k", transport=httpx.MockTransport(inboxes))
    d = client.get("/api/v1/mailboxes").json()
    assert d["errors"] == []
    emily, james = d["mailboxes"]
    assert (emily["name"], emily["campaignDaily"], emily["warmupState"], emily["todaySpam"], emily["needsAttention"]) == ("Emily Carter", 18, "warming", 1, False)
    assert james["needsAttention"] and james["lastError"] == "Token expired"
    s = d["summary"]
    assert (s["connected"], s["sendsLimit"], s["campaignDaily"], s["warmupTodaySends"], s["warmupWeekSent"]) == (2, 10000, 36, 12, 74)
    assert d["domains"] == [{"domain": "meetaheadtech360.com", "status": "active", "health": "warning", "problems": ["DMARC: No DMARC record."]}]
    assert d["campaigns"] == [{"name": "Coldbrew: DTF", "status": "draft", "dailySendCap": 48}]


def test_mailboxes_page_without_keys(client) -> None:
    app.dependency_overrides[get_optional_sequencer] = lambda: None
    app.dependency_overrides[get_optional_inboxes] = lambda: None
    d = client.get("/api/v1/mailboxes").json()
    assert d["mailboxes"] == [] and len(d["errors"]) == 2 and "CHEAPSEQUENCER_API_KEY" in d["errors"][0]


def test_a_rejected_key_says_so(client) -> None:
    app.dependency_overrides[get_optional_sequencer] = lambda: SequencerClient("bad", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    app.dependency_overrides[get_optional_inboxes] = lambda: None
    d = client.get("/api/v1/mailboxes").json()
    assert d["errors"][0] == "CheapSequencer rejected the API key. Check it in the backend's .env."
    json.dumps(d)
