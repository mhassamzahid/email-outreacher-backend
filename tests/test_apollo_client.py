"""The Apollo client against a fake Apollo (httpx.MockTransport). No network, no credits."""

import asyncio
import functools
import json

import httpx
import pytest

from services.apollo import ApolloClient, ApolloError

FREE_PLAN_403 = {
    "error": "The api/v1/mixed_people/api_search API is not included in your Free plan and is not accessible, even "
    "with a master key. All paid plans include full API access.",
    "error_code": "API_INACCESSIBLE",
    "error_details": {"code": "AUTH.AUTHORIZATION.ENDPOINT_ACCESS_DENIED"},
}


def sync(fn):
    """Run an async test with asyncio.run; pytest still sees the original signature for parametrize."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def client_with(handler) -> ApolloClient:
    return ApolloClient("test-key", transport=httpx.MockTransport(handler))


@sync
async def test_search_sends_domain_titles_seniorities_and_key() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = request.url.params.multi_items()
        seen["key"] = request.headers.get("x-api-key")
        body = {
            "total_entries": 7,
            "people": [
                {"id": "p1", "first_name": "Ava", "last_name_obfuscated": "Mo***n", "title": "Owner", "has_email": True,
                 "organization": {"name": "SameDayCustom"}},
                {"first_name": "No id"},
            ],
        }  # fmt: skip
        return httpx.Response(200, json=body)

    client = client_with(handler)
    people, total = await client.search_people("samedaycustom.com", ["Owner", "CEO"], ["owner", "founder"])
    await client.aclose()

    assert seen["path"] == "/api/v1/mixed_people/api_search"
    assert seen["key"] == "test-key"
    params = seen["params"]
    assert ("q_organization_domains_list[]", "samedaycustom.com") in params
    assert [v for k, v in params if k == "person_titles[]"] == ["Owner", "CEO"]
    assert [v for k, v in params if k == "person_seniorities[]"] == ["owner", "founder"]
    assert total == 7
    assert len(people) == 1  # the hit without an id is dropped
    p = people[0]
    assert (p.apollo_id, p.first_name, p.last_name_masked, p.title, p.has_email, p.organization_name) == (
        "p1", "Ava", "Mo***n", "Owner", True, "SameDayCustom",
    )  # fmt: skip


@sync
async def test_free_plan_refusal_passes_apollos_reason_through() -> None:
    client = client_with(lambda r: httpx.Response(403, json=FREE_PLAN_403))
    with pytest.raises(ApolloError) as exc:
        await client.search_people("x.com", [], [])
    await client.aclose()
    assert exc.value.status == 403
    assert "not included in your Free plan" in exc.value.message


@pytest.mark.parametrize(
    ("status", "text"),
    [(401, "rejected the API key"), (429, "600 calls an hour"), (500, "Apollo answered 500")],
)
@sync
async def test_other_errors_are_readable(status: int, text: str) -> None:
    client = client_with(lambda r: httpx.Response(status, text="Invalid API key" if status == 401 else "{}"))
    with pytest.raises(ApolloError) as exc:
        await client.search_people("x.com", [], [])
    await client.aclose()
    assert text in exc.value.message


@sync
async def test_reveal_parses_person_and_never_asks_for_personal_email() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        person = {
            "id": "p1", "first_name": "Ava", "last_name": "Morgan", "title": "Owner", "seniority": "owner",
            "email": "ava@samedaycustom.com", "email_status": "verified", "match_confidence": "high",
            "linkedin_url": "http://www.linkedin.com/in/ava-morgan",
            "organization": {"name": "SameDayCustom", "primary_domain": "samedaycustom.com"},
        }  # fmt: skip
        return httpx.Response(200, content=json.dumps({"person": person}))

    client = client_with(handler)
    person = await client.reveal("p1")
    await client.aclose()
    assert seen["path"] == "/api/v1/people/match"
    assert seen["params"] == {"id": "p1", "reveal_personal_emails": "false"}
    assert person and person.email == "ava@samedaycustom.com" and person.email_status == "verified"
    assert person.last_name == "Morgan" and person.organization_domain == "samedaycustom.com"


@sync
async def test_reveal_with_no_match_returns_none() -> None:
    client = client_with(lambda r: httpx.Response(200, json={"person": {"id": "p1", "match_confidence": "none"}}))
    assert await client.reveal("p1") is None
    await client.aclose()
