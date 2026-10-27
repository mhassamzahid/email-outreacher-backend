"""Apollo.io People API: find people at a company (free), then reveal one person's email (paid).

Search (`mixed_people/api_search`) costs 0 credits but hides the email, full last name and
LinkedIn URL. Enrichment (`people/match`) returns them and costs credits: about 1 per person for
the email, more if a phone comes back (we never ask for phones), and nothing if no match is found.
Both are limited to 600 calls an hour.
"""

from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.apollo.io/api/v1"

EMAIL_STATUSES = ("verified", "unverified", "likely to engage", "unavailable")

SENIORITIES = ("owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry", "intern")


class ApolloError(Exception):
    """Apollo refused or failed. `message` is written for the person using the app."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class FoundPerson:
    """A search hit. The last name is masked ("Hu***n") until the person is revealed."""

    apollo_id: str
    first_name: str
    last_name_masked: str
    title: str
    has_email: bool
    organization_name: str | None
    raw: dict[str, Any]


@dataclass
class RevealedPerson:
    apollo_id: str
    first_name: str
    last_name: str
    title: str
    seniority: str | None
    email: str | None
    # As Apollo reports it: verified, unverified, likely to engage, unavailable...
    email_status: str | None
    linkedin_url: str | None
    organization_name: str | None
    organization_domain: str | None
    match_confidence: str | None
    raw: dict[str, Any]


def _message(response: httpx.Response) -> str:
    if response.status_code == 401:
        return "Apollo rejected the API key. Check APOLLO_API_KEY."
    if response.status_code == 403:
        # Apollo says why (e.g. "not included in your Free plan"); that's more useful than a guess.
        try:
            reason = response.json().get("error")
        except ValueError:
            reason = None
        return reason or "This Apollo API key isn't allowed to call this endpoint."
    if response.status_code == 429:
        return "Apollo's limit of 600 calls an hour was reached. Try again later."
    try:
        body = response.json()
        detail = (body.get("error_details") or {}).get("message") or body.get("error") or body.get("message")
    except ValueError:
        detail = response.text[:200]
    return f"Apollo answered {response.status_code}: {detail or 'no details'}"


class ApolloClient:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"x-api-key": api_key, "Cache-Control": "no-cache", "accept": "application/json"},
            timeout=30,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, params=params)
        except httpx.HTTPError as exc:
            raise ApolloError(f"Couldn't reach Apollo: {exc}", 502) from exc
        if response.status_code != 200:
            raise ApolloError(_message(response), response.status_code)
        return response.json()

    async def search_people(
        self,
        domain: str | None,
        titles: list[str],
        seniorities: list[str],
        per_page: int = 25,
        *,
        page: int = 1,
        organization_locations: list[str] | None = None,
        keywords: str | None = None,
        employee_ranges: list[str] | None = None,
        email_statuses: list[str] | None = None,
    ) -> tuple[list[FoundPerson], int]:
        """People matching the filters: at one company domain, or anywhere that fits an ICP. 0 credits.

        `employee_ranges` are "min,max" strings ("1,10"). `organization_locations` are cities, states,
        countries or postcodes of the company's headquarters. `keywords` is free text; Apollo doesn't
        say whether it matches the person or the company, so treat it as a broad filter.
        """
        params: list[tuple[str, str]] = [("page", str(page)), ("per_page", str(per_page))]
        if domain:
            params.append(("q_organization_domains_list[]", domain))
        params += [("person_titles[]", t) for t in titles]
        params += [("person_seniorities[]", s) for s in seniorities]
        params += [("organization_locations[]", loc) for loc in organization_locations or []]
        params += [("organization_num_employees_ranges[]", r) for r in employee_ranges or []]
        params += [("contact_email_status[]", st) for st in email_statuses or []]
        if keywords:
            params.append(("q_keywords", keywords))
        body = await self._post("/mixed_people/api_search", params)
        people = [
            FoundPerson(
                apollo_id=p["id"],
                first_name=p.get("first_name") or "",
                last_name_masked=p.get("last_name_obfuscated") or p.get("last_name") or "",
                title=p.get("title") or "",
                has_email=bool(p.get("has_email")),
                organization_name=(p.get("organization") or {}).get("name"),
                raw=p,
            )
            for p in body.get("people") or []
            if p.get("id")
        ]
        return people, int(body.get("total_entries") or len(people))

    async def reveal(self, apollo_id: str | None = None, *, linkedin_url: str | None = None) -> RevealedPerson | None:
        """Full name, work email and LinkedIn for one person, by Apollo id or LinkedIn URL.

        Costs credits when data comes back.
        """
        if not apollo_id and not linkedin_url:
            raise ValueError("Pass an Apollo id or a LinkedIn URL")
        who = [("id", apollo_id)] if apollo_id else [("linkedin_url", linkedin_url)]
        body = await self._post("/people/match", [*who, ("reveal_personal_emails", "false")])
        p = body.get("person")
        if not p or p.get("match_confidence") == "none":
            return None
        org = p.get("organization") or {}
        return RevealedPerson(
            apollo_id=p.get("id") or apollo_id or "",
            first_name=p.get("first_name") or "",
            last_name=p.get("last_name") or "",
            title=p.get("title") or "",
            seniority=p.get("seniority"),
            email=(p.get("email") or None),
            email_status=p.get("email_status"),
            linkedin_url=p.get("linkedin_url"),
            organization_name=org.get("name"),
            organization_domain=org.get("primary_domain"),
            match_confidence=p.get("match_confidence"),
            raw=p,
        )
