from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from dependencies import get_current_user, get_icp_profile_repo, get_lead_intel_repo, get_workspace_company_repo
from main import app
from services.auth import CurrentUser

NOW = datetime(2026, 10, 27, tzinfo=UTC)
ICP_ID = uuid4()
WORKSPACE_ID = uuid4()
COMPANY_ID = uuid4()
ADMIN = CurrentUser(uuid4(), "admin@example.com", "Admin", "admin")

ICP_ROW = {
    "id": ICP_ID,
    "name": "Med spas",
    "industry": "Beauty",
    "priority": 10,
    "enabled": True,
    "sources": ["website_text", "instagram_bio"],
    "any_of": ["med spa"],
    "none_of": ["school"],
    "website_any_of": [],
    "instagram_any_of": ["botox"],
    "require_all": False,
    "min_followers": 500,
    "max_followers": None,
    "search_terms": ["med spa"],
    "requires_website": True,
    "min_reviews": 10,
    "places": ["Austin, TX"],
    "company_count": 1,
    "created_at": NOW,
    "updated_at": NOW,
}

COMPANY_ROW = {
    "id": COMPANY_ID,
    "display_name": "Glow Med Spa",
    "status": "active",
    "city": "Austin",
    "state": "TX",
    "country": "us",
    "icp_id": ICP_ID,
    "icp_name": "Med spas",
    "tier": 1,
    "problem_score": 72.5,
    "website_status": "has_website",
    "domain": "glowmedspa.com",
    "created_at": NOW,
    "fields": {
        "website_url": "https://glowmedspa.com",
        "instagram_handle": "glowmedspa",
        "instagram_followers": "4210",
        "instagram_url": "https://www.instagram.com/glowmedspa/",
        "website_text": "Botox and fillers in Austin.",
    },
}


class FakeLeadIntelRepo:
    async def list_icps(self):
        return [ICP_ROW]

    async def get_icp(self, icp_id: UUID):
        return ICP_ROW if icp_id == ICP_ID else None

    async def list_companies(self, icp_id, q, limit, offset, *, no_icp=False, only_ids=None, exclude_ids=None):
        rows = [COMPANY_ROW] if icp_id in (None, ICP_ID) and not no_icp else []
        if only_ids is not None:
            rows = [r for r in rows if r["id"] in only_ids]
        if exclude_ids:
            rows = [r for r in rows if r["id"] not in exclude_ids]
        return rows, len(rows)

    async def contactable_ids(self, ids=None, icp_id=None):
        known = [COMPANY_ID]
        if icp_id is not None:
            known = known if icp_id == ICP_ID else []
        return [i for i in known if ids is None or i in ids]

    async def get_company(self, company_id: UUID):
        return COMPANY_ROW if company_id == COMPANY_ID else None


class FakeIcpProfileRepo:
    def __init__(self) -> None:
        self.saved: dict[tuple[UUID, UUID], SimpleNamespace] = {}

    async def workspace_exists(self, workspace_id):
        return workspace_id == WORKSPACE_ID

    async def get_many(self, workspace_id, icp_ids):
        return {i: self.saved[(workspace_id, i)] for i in icp_ids if (workspace_id, i) in self.saved}

    async def upsert(self, workspace_id, icp_id, values):
        self.saved[(workspace_id, icp_id)] = SimpleNamespace(lead_intel_icp_id=icp_id, updated_at=NOW, **values)
        return self.saved[(workspace_id, icp_id)]


class FakeWorkspaceCompanyRepo:
    def __init__(self) -> None:
        self.links: dict[tuple[UUID, UUID], datetime] = {}

    async def workspace_exists(self, workspace_id):
        return workspace_id == WORKSPACE_ID

    async def imported(self, workspace_id):
        return {cid: at for (ws, cid), at in self.links.items() if ws == workspace_id}

    async def add(self, workspace_id, company_ids):
        new = [cid for cid in dict.fromkeys(company_ids) if (workspace_id, cid) not in self.links]
        for cid in new:
            self.links[(workspace_id, cid)] = NOW
        return len(new)

    async def remove(self, workspace_id, company_ids):
        gone = [cid for cid in company_ids if self.links.pop((workspace_id, cid), None)]
        return len(gone)


@pytest.fixture
def workspace_companies() -> FakeWorkspaceCompanyRepo:
    return FakeWorkspaceCompanyRepo()


@pytest.fixture
def profiles() -> FakeIcpProfileRepo:
    return FakeIcpProfileRepo()


@pytest.fixture
def client(profiles: FakeIcpProfileRepo, workspace_companies: FakeWorkspaceCompanyRepo):
    app.dependency_overrides[get_lead_intel_repo] = FakeLeadIntelRepo
    app.dependency_overrides[get_workspace_company_repo] = lambda: workspace_companies
    app.dependency_overrides[get_icp_profile_repo] = lambda: profiles
    # Signed in as an admin, who opens every workspace. tests/test_auth.py covers the other roles.
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    yield TestClient(app)
    app.dependency_overrides.clear()
