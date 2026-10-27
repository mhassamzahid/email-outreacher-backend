from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from config import get_settings
from crud import IcpProfileRepo
from dependencies import (
    get_icp_profile_repo,
    get_lead_intel_icp_writer,
    get_lead_intel_repo,
    get_workspace_id,
    require_workspace_id,
)
from lead_intel import LeadIntelRepo
from lead_intel_writes import LeadIntelIcpWriter
from schemas import ICP_SOURCES, Icp, IcpCreateIn, IcpProfileIn, IcpProfileOut


router = APIRouter(prefix="/icps", tags=["icps"])


@router.get("", response_model=list[Icp])
async def list_icps(
    workspace_id: UUID | None = Depends(get_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> list[Icp]:
    """Lead Intel's ICPs. With X-Workspace-Id, each carries that workspace's outreach profile."""
    rows = await lead_intel.list_icps()
    by_id = await profiles.get_many(workspace_id, [row["id"] for row in rows]) if workspace_id else {}
    return [Icp.model_validate({**row, "profile": by_id.get(row["id"])}) for row in rows]


def _clean(terms: list[str]) -> list[str]:
    """Trimmed, blanks dropped, duplicates removed (ignoring case), order kept."""
    seen: set[str] = set()
    out = []
    for term in (t.strip() for t in terms):
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    return out


@router.post("", response_model=Icp, status_code=201)
async def create_icp(
    body: IcpCreateIn,
    writer: LeadIntelIcpWriter = Depends(get_lead_intel_icp_writer),
) -> Icp:
    """Add a new ICP to Lead Intel's icps table, shared by both projects. Insert-only: nothing that
    already exists in Lead Intel is changed. Lead Intel assigns businesses to it on its next match."""
    created_by = get_settings().lead_intel_icp_created_by
    if not created_by:
        raise HTTPException(status_code=503, detail="LEAD_INTEL_ICP_CREATED_BY is not set")
    name, industry = body.name.strip(), body.industry.strip()
    if not name or not industry:
        raise HTTPException(status_code=422, detail="An ICP needs a name and an industry")
    if bad := [x for x in body.sources if x not in ICP_SOURCES]:
        raise HTTPException(status_code=422, detail=f"Unknown sources: {', '.join(bad)}")
    if body.min_followers is not None and body.max_followers is not None and body.min_followers > body.max_followers:
        raise HTTPException(status_code=422, detail="Fewest followers can't be more than most followers")
    if await writer.name_taken(name):
        raise HTTPException(status_code=409, detail="An ICP with this name already exists in Lead Intel")

    values = body.model_dump()
    values |= {
        "name": name,
        "industry": industry,
        "sources": list(dict.fromkeys(body.sources)),
        **{key: _clean(values[key]) for key in ("any_of", "none_of", "website_any_of", "instagram_any_of", "search_terms", "places")},
    }
    row = await writer.add_icp(created_by, values)
    return Icp.model_validate({**row, "company_count": 0, "profile": None})


@router.get("/{icp_id}", response_model=Icp)
async def get_icp(
    icp_id: UUID,
    workspace_id: UUID | None = Depends(get_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> Icp:
    row = await lead_intel.get_icp(icp_id)
    if not row:
        raise HTTPException(status_code=404, detail="ICP not found")
    by_id = await profiles.get_many(workspace_id, [icp_id]) if workspace_id else {}
    return Icp.model_validate({**row, "profile": by_id.get(icp_id)})


@router.put("/{icp_id}/profile", response_model=IcpProfileOut)
async def save_icp_profile(
    icp_id: UUID,
    body: IcpProfileIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> IcpProfileOut:
    """Save a workspace's outreach settings for a Lead Intel ICP. Writes `outreach.eo_icp_profiles` only."""
    if not await profiles.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    if not await lead_intel.get_icp(icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")
    profile = await profiles.upsert(workspace_id, icp_id, body.model_dump())
    return IcpProfileOut.model_validate(profile)
