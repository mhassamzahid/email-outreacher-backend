from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import routers.stakeholders as stakeholders_router
from database import get_session
from dependencies import get_apollo, get_provider_call_repo, get_stakeholder_repo
from main import app
from services.apollo import ApolloError, FoundPerson, RevealedPerson
from tests.conftest import COMPANY_ID, WORKSPACE_ID

WS = {"X-Workspace-Id": str(WORKSPACE_ID)}
FREE_PLAN = "The api/v1/mixed_people/api_search API is not included in your Free plan."


class FakeApollo:
    def __init__(self) -> None:
        self.search_error: ApolloError | None = None
        self.reveals = 0
        self.searched: list[tuple] = []

    async def search_people(self, domain, titles, seniorities, per_page=25):
        if self.search_error:
            raise self.search_error
        self.searched.append((domain, titles, seniorities))
        return [FoundPerson("ap1", "Ava", "Mo***n", "Owner", True, "Glow", {}),
                FoundPerson("ap2", "Liam", "Ca***r", "Founder", False, "Glow", {})], 12  # fmt: skip

    async def reveal(self, apollo_id):
        self.reveals += 1
        if apollo_id == "ap2":
            return None
        return RevealedPerson(apollo_id, "Ava", "Morgan", "Owner", "owner", "ava@glowmedspa.com", "verified",
                              "https://www.linkedin.com/in/ava", "Glow", "glowmedspa.com", "high", {})  # fmt: skip


class FakeStakeholders:
    def __init__(self) -> None:
        self.rows: dict = {}

    def _row(self, ws, company_id, p):
        return SimpleNamespace(
            id=uuid4(), workspace_id=ws, lead_intel_business_id=company_id, source="apollo", external_id=p.apollo_id,
            first_name=p.first_name, last_name=p.last_name_masked, title=p.title, seniority=None, linkedin_url=None,
            has_email=p.has_email, email=None, email_status=None, revealed_at=None, lead_id=None,
            found_at=datetime.now(UTC),
        )  # fmt: skip

    async def list(self, ws, company_id=None):
        return [r for r in self.rows.values() if company_id in (None, r.lead_intel_business_id)]

    async def get_many(self, ws, ids):
        return [r for r in self.rows.values() if r.id in ids]

    async def save_found(self, ws, company_id, people):
        new = 0
        for p in people:
            if not any(r.external_id == p.apollo_id for r in self.rows.values()):
                row = self._row(ws, company_id, p)
                self.rows[row.id] = row
                new += 1
        return new

    async def save_revealed(self, s, person, lead_id):
        s.revealed_at = datetime.now(UTC)
        s.lead_id = lead_id
        if person:
            s.last_name, s.email, s.email_status = person.last_name, person.email, person.email_status
        else:
            s.email_status = "no_match"


class FakeCalls:
    def __init__(self) -> None:
        self.records: list[tuple] = []

    async def record(self, ws, provider, endpoint, status, *, key=None, lead_id=None, credits=None):
        self.records.append((provider, endpoint, status))


@pytest.fixture
def apollo_env(client, workspace_companies, monkeypatch):
    apollo, rows, calls = FakeApollo(), FakeStakeholders(), FakeCalls()
    app.dependency_overrides[get_apollo] = lambda: apollo
    app.dependency_overrides[get_stakeholder_repo] = lambda: rows
    app.dependency_overrides[get_provider_call_repo] = lambda: calls
    app.dependency_overrides[get_session] = lambda: None
    leads_made = []

    async def fake_lead(session, ws, icp_id, company_id, lead, apollo_id, email_status):
        leads_made.append(lead)
        return uuid4(), True

    monkeypatch.setattr(stakeholders_router, "lead_from_stakeholder", fake_lead)
    workspace_companies.links[(WORKSPACE_ID, COMPANY_ID)] = datetime.now(UTC)
    return SimpleNamespace(apollo=apollo, rows=rows, calls=calls, leads=leads_made)


def search(client, **body):
    return client.post("/api/v1/stakeholders/search", json={"companyIds": [str(COMPANY_ID)], **body}, headers=WS)


def test_search_saves_people_by_company_domain(client, apollo_env) -> None:
    result = search(client, titles=["Owner", " "], seniorities=["owner"]).json()
    assert result["error"] is None
    [r] = result["results"]
    assert (r["status"], r["found"], r["new"], r["totalAtApollo"]) == ("searched", 2, 2, 12)
    assert apollo_env.apollo.searched == [("glowmedspa.com", ["Owner"], ["owner"])]
    assert apollo_env.calls.records == [("apollo", "mixed_people/api_search", "succeeded")]

    again = search(client).json()["results"][0]
    assert again["new"] == 0

    listed = client.get(f"/api/v1/stakeholders?companyId={COMPANY_ID}", headers=WS).json()
    assert {s["lastName"] for s in listed} == {"Mo***n", "Ca***r"}


def test_free_plan_refusal_is_reported_not_crashed(client, apollo_env) -> None:
    apollo_env.apollo.search_error = ApolloError(FREE_PLAN, 403)
    result = search(client).json()
    assert result["error"] == FREE_PLAN
    assert result["results"][0]["status"] == "failed"
    assert apollo_env.rows.rows == {}


def test_search_skips_companies_not_in_the_workspace(client, apollo_env) -> None:
    other = str(uuid4())
    result = client.post("/api/v1/stakeholders/search", json={"companyIds": [other]}, headers=WS).json()
    assert result["results"][0]["status"] == "skipped"
    assert apollo_env.apollo.searched == []


def test_unknown_seniority_is_422(client, apollo_env) -> None:
    assert search(client, seniorities=["boss"]).status_code == 422


def test_reveal_creates_leads_and_never_pays_twice(client, apollo_env) -> None:
    search(client)
    ids = [str(s.id) for s in apollo_env.rows.rows.values()]
    result = client.post("/api/v1/stakeholders/reveal", json={"stakeholderIds": ids}, headers=WS).json()
    statuses = sorted(o["status"] for o in result["outcomes"])
    assert statuses == ["no_match", "revealed"] and result["revealed"] == 1
    revealed = next(o for o in result["outcomes"] if o["status"] == "revealed")
    assert revealed["email"] == "ava@glowmedspa.com" and revealed["leadCreated"]
    [lead] = apollo_env.leads
    assert (lead.email, lead.company, lead.domain) == ("ava@glowmedspa.com", "Glow Med Spa", "glowmedspa.com")

    again = client.post("/api/v1/stakeholders/reveal", json={"stakeholderIds": ids}, headers=WS).json()
    assert {o["status"] for o in again["outcomes"]} == {"already_revealed"}
    assert apollo_env.apollo.reveals == 2  # once each, never again


def test_reveal_limits_and_workspace(client, apollo_env) -> None:
    too_many = [str(uuid4()) for _ in range(26)]
    assert client.post("/api/v1/stakeholders/reveal", json={"stakeholderIds": too_many}, headers=WS).status_code == 422
    assert client.post("/api/v1/stakeholders/reveal", json={"stakeholderIds": [str(uuid4())]}).status_code == 400


def test_missing_key_is_503(client, workspace_companies, monkeypatch) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "apollo_api_key", None)
    app.dependency_overrides.pop(get_apollo, None)
    assert search(client).status_code == 503
