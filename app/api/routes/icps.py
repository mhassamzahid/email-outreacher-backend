from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_icp_profile_repo, get_lead_intel_repo
from app.repositories.icp_profiles import IcpProfileRepo
from app.repositories.lead_intel import LeadIntelRepo
from app.schemas.icps import Icp, IcpProfileIn, IcpProfileOut

router = APIRouter(prefix="/icps", tags=["icps"])


@router.get("", response_model=list[Icp])
async def list_icps(
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> list[Icp]:
    rows = await lead_intel.list_icps()
    by_id = await profiles.get_many([row["id"] for row in rows])
    return [Icp.model_validate({**row, "profile": by_id.get(row["id"])}) for row in rows]


@router.get("/{icp_id}", response_model=Icp)
async def get_icp(
    icp_id: UUID,
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> Icp:
    row = await lead_intel.get_icp(icp_id)
    if not row:
        raise HTTPException(status_code=404, detail="ICP not found")
    by_id = await profiles.get_many([icp_id])
    return Icp.model_validate({**row, "profile": by_id.get(icp_id)})


@router.put("/{icp_id}/profile", response_model=IcpProfileOut)
async def save_icp_profile(
    icp_id: UUID,
    body: IcpProfileIn,
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> IcpProfileOut:
    """Save the outreach settings for a Lead Intel ICP. Writes to `outreach.icp_profiles` only."""
    if not await lead_intel.get_icp(icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")
    profile = await profiles.upsert(icp_id, body.model_dump())
    return IcpProfileOut.model_validate(profile)
