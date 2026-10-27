from uuid import uuid4

from tests.conftest import ICP_ID


def test_list_icps_without_profile(client) -> None:
    response = client.get("/api/v1/icps")
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
    saved = client.put(f"/api/v1/icps/{ICP_ID}/profile", json=body)
    assert saved.status_code == 200
    assert saved.json()["titles"] == ["Owner", "Founder"]

    profile = client.get(f"/api/v1/icps/{ICP_ID}").json()["profile"]
    assert profile["offer"] == "More bookings"
    assert profile["enabledSources"] == ["instagram"]


def test_save_profile_for_unknown_icp_is_404(client) -> None:
    assert client.put(f"/api/v1/icps/{uuid4()}/profile", json={}).status_code == 404
