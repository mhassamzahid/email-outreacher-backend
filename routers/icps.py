from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from crud import IcpProfileRepo
from dependencies import get_icp_profile_repo, get_lead_intel_repo, get_workspace_id, require_workspace_id
from lead_intel import LeadIntelRepo
from schemas import Icp, IcpProfileIn, IcpProfileOut


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
