from uuid import uuid4

from tests.conftest import COMPANY_ID, ICP_ID


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
