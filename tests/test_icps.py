from uuid import uuid4

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
