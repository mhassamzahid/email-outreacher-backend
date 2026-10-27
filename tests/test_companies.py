from uuid import uuid4

from tests.conftest import COMPANY_ID, ICP_ID, WORKSPACE_ID


def test_list_companies_for_icp(client) -> None:
    response = client.get(f"/api/v1/icps/{ICP_ID}/companies")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    [company] = page["items"]
    assert company["name"] == "Glow Med Spa"
    assert company["websiteUrl"] == "https://glowmedspa.com"
    assert company["domain"] == "glowmedspa.com"
    assert company["instagram"]["handle"] == "glowmedspa"
    assert company["instagram"]["followers"] == 4210
    assert "fields" not in company


def test_company_detail_includes_all_fields(client) -> None:
    detail = client.get(f"/api/v1/companies/{COMPANY_ID}").json()
    assert detail["fields"]["website_text"] == "Botox and fillers in Austin."


def test_unknown_company_is_404(client) -> None:
    assert client.get(f"/api/v1/companies/{uuid4()}").status_code == 404


def test_limit_is_capped(client) -> None:
    assert client.get("/api/v1/companies?limit=500").status_code == 422


WS = {"X-Workspace-Id": str(WORKSPACE_ID)}


def test_import_by_ids_then_list_workspace_only(client) -> None:
    result = client.post("/api/v1/companies/import", json={"companyIds": [str(COMPANY_ID), str(COMPANY_ID)]}, headers=WS)
    assert result.status_code == 200, result.text
    assert result.json() == {"imported": 1, "alreadyInWorkspace": 0, "notFound": []}

    mine = client.get("/api/v1/companies?inWorkspace=true", headers=WS).json()
    assert mine["total"] == 1 and mine["items"][0]["importedAt"] is not None
    assert client.get("/api/v1/companies?inWorkspace=false", headers=WS).json()["total"] == 0


def test_import_again_counts_as_already_in_workspace(client) -> None:
    client.post("/api/v1/companies/import", json={"icpId": str(ICP_ID)}, headers=WS)
    again = client.post("/api/v1/companies/import", json={"icpId": str(ICP_ID)}, headers=WS).json()
    assert again == {"imported": 0, "alreadyInWorkspace": 1, "notFound": []}


def test_unknown_or_excluded_ids_are_reported_not_imported(client) -> None:
    stranger = str(uuid4())
    result = client.post("/api/v1/companies/import", json={"companyIds": [stranger]}, headers=WS).json()
    assert result == {"imported": 0, "alreadyInWorkspace": 0, "notFound": [stranger]}


def test_import_needs_exactly_one_of_ids_or_icp(client) -> None:
    assert client.post("/api/v1/companies/import", json={}, headers=WS).status_code == 422
    both = {"companyIds": [str(COMPANY_ID)], "icpId": str(ICP_ID)}
    assert client.post("/api/v1/companies/import", json=both, headers=WS).status_code == 422
    assert client.post("/api/v1/companies/import", json={"icpId": str(uuid4())}, headers=WS).status_code == 404


def test_import_needs_a_real_workspace(client) -> None:
    body = {"companyIds": [str(COMPANY_ID)]}
    assert client.post("/api/v1/companies/import", json=body).status_code == 400
    assert client.post("/api/v1/companies/import", json=body, headers={"X-Workspace-Id": str(uuid4())}).status_code == 404


def test_remove_from_workspace(client) -> None:
    client.post("/api/v1/companies/import", json={"companyIds": [str(COMPANY_ID)]}, headers=WS)
    removed = client.post("/api/v1/companies/remove", json={"companyIds": [str(COMPANY_ID)]}, headers=WS).json()
    assert removed == {"removed": 1}
    assert client.get("/api/v1/companies?inWorkspace=true", headers=WS).json()["total"] == 0


def test_in_workspace_filter_needs_header(client) -> None:
    assert client.get("/api/v1/companies?inWorkspace=true").status_code == 400
