from uuid import uuid4

import pytest

from tests.conftest import ICP_ID, WORKSPACE_ID

WS = {"X-Workspace-Id": str(WORKSPACE_ID)}


def test_list_icps_without_profile(client) -> None:
    response = client.get("/api/v1/icps", headers=WS)
    assert response.status_code == 200
    [icp] = response.json()
    assert icp["id"] == str(ICP_ID)
    assert icp["searchTerms"] == ["med spa"]
    assert icp["companyCount"] == 1
    assert icp["profile"] is None
    # Every Lead Intel field comes through, camelCased.
    assert (icp["sources"], icp["anyOf"], icp["noneOf"], icp["instagramAnyOf"]) == (
        ["website_text", "instagram_bio"], ["med spa"], ["school"], ["botox"],
    )  # fmt: skip
    assert (icp["requireAll"], icp["minFollowers"], icp["maxFollowers"], icp["requiresWebsite"], icp["minReviews"]) == (
        False, 500, None, True, 10,
    )  # fmt: skip


def test_get_unknown_icp_is_404(client) -> None:
    assert client.get(f"/api/v1/icps/{uuid4()}").status_code == 404


def test_save_profile_then_read_it_back(client) -> None:
    body = {"titles": ["Owner", "Founder"], "offer": "More bookings", "enabledSources": ["instagram"]}
    saved = client.put(f"/api/v1/icps/{ICP_ID}/profile", json=body, headers=WS)
    assert saved.status_code == 200
    assert saved.json()["titles"] == ["Owner", "Founder"]

    profile = client.get(f"/api/v1/icps/{ICP_ID}", headers=WS).json()["profile"]
    assert profile["offer"] == "More bookings"
    assert profile["enabledSources"] == ["instagram"]


def test_profiles_are_per_workspace(client) -> None:
    client.put(f"/api/v1/icps/{ICP_ID}/profile", json={"offer": "x"}, headers=WS)
    assert client.get(f"/api/v1/icps/{ICP_ID}", headers={"X-Workspace-Id": str(uuid4())}).json()["profile"] is None
    assert client.get(f"/api/v1/icps/{ICP_ID}").json()["profile"] is None


def test_save_profile_needs_a_workspace(client) -> None:
    assert client.put(f"/api/v1/icps/{ICP_ID}/profile", json={}).status_code == 400
    other = {"X-Workspace-Id": str(uuid4())}
    assert client.put(f"/api/v1/icps/{ICP_ID}/profile", json={}, headers=other).status_code == 404


def test_save_profile_for_unknown_icp_is_404(client) -> None:
    assert client.put(f"/api/v1/icps/{uuid4()}/profile", json={}, headers=WS).status_code == 404


class FakeIcpWriter:
    def __init__(self) -> None:
        self.added: list[dict] = []

    async def name_taken(self, name):
        return name.lower() in {"med spas", *(a["name"].lower() for a in self.added)}

    async def add_icp(self, created_by, values):
        from datetime import UTC, datetime

        row = {"id": uuid4(), "created_by": created_by, "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), **values}
        self.added.append(row)
        return row


@pytest.fixture
def writer(client, monkeypatch):
    from config import get_settings
    from dependencies import get_lead_intel_icp_writer
    from main import app

    fake = FakeIcpWriter()
    app.dependency_overrides[get_lead_intel_icp_writer] = lambda: fake
    monkeypatch.setattr(get_settings(), "lead_intel_icp_created_by", uuid4())
    return fake


NEW_ICP = {
    "name": "  Sign shops ",
    "industry": "Printing",
    "priority": 30,
    "sources": ["website_text", "instagram_bio", "website_text"],
    "anyOf": ["sign shop", " Sign Shop ", "", "vinyl signs"],
    "noneOf": ["school"],
    "minFollowers": 100,
    "maxFollowers": 50000,
    "searchTerms": ["sign shop"],
    "places": ["Chicago"],
}


def test_create_icp_with_lead_intel_fields(client, writer) -> None:
    created = client.post("/api/v1/icps", json=NEW_ICP)
    assert created.status_code == 201, created.text
    icp = created.json()
    assert (icp["name"], icp["industry"], icp["priority"], icp["companyCount"], icp["profile"]) == ("Sign shops", "Printing", 30, 0, None)
    assert icp["sources"] == ["website_text", "instagram_bio"]
    assert icp["anyOf"] == ["sign shop", "vinyl signs"]  # trimmed, blanks and repeats dropped
    [row] = writer.added
    assert row["created_by"] is not None and row["min_followers"] == 100


def test_creating_an_icp_sets_up_its_campaign(client, writer) -> None:
    from dependencies import get_optional_sequencer
    from main import app
    from services.sending import SendingError

    class Down:
        async def campaigns(self):
            raise SendingError("Couldn't reach CheapSequencer: ConnectError")

    app.dependency_overrides[get_optional_sequencer] = lambda: Down()
    created = client.post("/api/v1/icps", json=NEW_ICP)
    # The ICP is still made; the campaign problem is reported, not raised.
    assert created.status_code == 201 and len(writer.added) == 1
    assert created.json()["campaignNote"].startswith("Campaign not set up: Couldn't reach CheapSequencer")


def test_create_icp_validation(client, writer) -> None:
    assert client.post("/api/v1/icps", json={**NEW_ICP, "name": "Med Spas"}).status_code == 409
    assert client.post("/api/v1/icps", json={**NEW_ICP, "industry": "  "}).status_code == 422
    assert client.post("/api/v1/icps", json={**NEW_ICP, "sources": ["tiktok"]}).status_code == 422
    assert client.post("/api/v1/icps", json={**NEW_ICP, "minFollowers": 10, "maxFollowers": 5}).status_code == 422
    assert writer.added == []


def test_create_icp_needs_created_by(client, writer, monkeypatch) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "lead_intel_icp_created_by", None)
    assert client.post("/api/v1/icps", json=NEW_ICP).status_code == 503
