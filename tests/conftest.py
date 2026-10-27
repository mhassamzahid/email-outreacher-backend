from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_icp_profile_repo, get_lead_intel_repo
from app.main import app

NOW = datetime(2026, 10, 27, tzinfo=UTC)
ICP_ID = uuid4()
COMPANY_ID = uuid4()

ICP_ROW = {
    "id": ICP_ID,
    "name": "Med spas",
    "industry": "Beauty",
    "priority": 10,
    "enabled": True,
    "search_terms": ["med spa"],
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
        "website_text": "Botox and fillers in Austin.",
    },
}


class FakeLeadIntelRepo:
    async def list_icps(self):
        return [ICP_ROW]

    async def get_icp(self, icp_id: UUID):
        return ICP_ROW if icp_id == ICP_ID else None

    async def list_companies(self, icp_id, q, limit, offset):
        rows = [COMPANY_ROW] if icp_id in (None, ICP_ID) else []
        return rows, len(rows)

    async def get_company(self, company_id: UUID):
        return COMPANY_ROW if company_id == COMPANY_ID else None


class FakeIcpProfileRepo:
    def __init__(self) -> None:
        self.saved: dict[UUID, SimpleNamespace] = {}

    async def get_many(self, icp_ids):
        return {i: self.saved[i] for i in icp_ids if i in self.saved}

    async def upsert(self, icp_id, values):
        self.saved[icp_id] = SimpleNamespace(lead_intel_icp_id=icp_id, updated_at=NOW, **values)
        return self.saved[icp_id]


@pytest.fixture
def profiles() -> FakeIcpProfileRepo:
    return FakeIcpProfileRepo()


@pytest.fixture
def client(profiles: FakeIcpProfileRepo):
    app.dependency_overrides[get_lead_intel_repo] = FakeLeadIntelRepo
    app.dependency_overrides[get_icp_profile_repo] = lambda: profiles
    yield TestClient(app)
    app.dependency_overrides.clear()
