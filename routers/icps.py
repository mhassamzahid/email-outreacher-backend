from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from config import get_settings
from crud import IcpProfileRepo, ProductRepo, PromptRepo
from dependencies import (
    get_icp_profile_repo,
    get_lead_intel_icp_writer,
    get_lead_intel_repo,
    get_product_repo,
    get_prompt_repo,
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


def _validated(body: IcpCreateIn) -> dict:
    """The ICP's values, cleaned the same way Lead Intel's own form does; 422 for anything invalid."""
    name, industry = body.name.strip(), body.industry.strip()
    if not name or not industry:
        raise HTTPException(status_code=422, detail="An ICP needs a name and an industry")
    if bad := [x for x in body.sources if x not in ICP_SOURCES]:
        raise HTTPException(status_code=422, detail=f"Unknown sources: {', '.join(bad)}")
    if body.min_followers is not None and body.max_followers is not None and body.min_followers > body.max_followers:
        raise HTTPException(status_code=422, detail="Fewest followers can't be more than most followers")
    values = body.model_dump()
    return values | {
        "name": name,
        "industry": industry,
        "sources": list(dict.fromkeys(body.sources)),
        **{key: _clean(values[key]) for key in ("any_of", "none_of", "website_any_of", "instagram_any_of", "search_terms", "places")},
    }


@router.post("", response_model=Icp, status_code=201)
async def create_icp(
    body: IcpCreateIn,
    writer: LeadIntelIcpWriter = Depends(get_lead_intel_icp_writer),
) -> Icp:
    """Add a new ICP to Lead Intel's icps table, shared by both projects. Lead Intel assigns businesses
    to it on its next match."""
    created_by = get_settings().lead_intel_icp_created_by
    if not created_by:
        raise HTTPException(status_code=503, detail="LEAD_INTEL_ICP_CREATED_BY is not set")
    values = _validated(body)
    if await writer.name_taken(values["name"]):
        raise HTTPException(status_code=409, detail="An ICP with this name already exists in Lead Intel")
    row = await writer.add_icp(created_by, values)
    return Icp.model_validate({**row, "company_count": 0, "profile": None})


@router.put("/{icp_id}", response_model=Icp)
async def update_icp(
    icp_id: UUID,
    body: IcpCreateIn,
    workspace_id: UUID | None = Depends(get_workspace_id),
    writer: LeadIntelIcpWriter = Depends(get_lead_intel_icp_writer),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
) -> Icp:
    """Edit an ICP's own fields in Lead Intel's icps table (shared: Lead Intel sees the change too).
    Only that ICP's row is updated; its businesses are re-matched by Lead Intel on its next run."""
    values = _validated(body)
    if await writer.name_taken(values["name"], except_id=icp_id):
        raise HTTPException(status_code=409, detail="Another ICP in Lead Intel already has this name")
    if await writer.update_icp(icp_id, values) is None:
        raise HTTPException(status_code=404, detail="ICP not found")
    # Read it back the usual way, so the answer matches GET /icps/{id} (company count, profile).
    row = await lead_intel.get_icp(icp_id)
    by_id = await profiles.get_many(workspace_id, [icp_id]) if workspace_id else {}
    return Icp.model_validate({**row, "profile": by_id.get(icp_id)})


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
    prompts: PromptRepo = Depends(get_prompt_repo),
    products: ProductRepo = Depends(get_product_repo),
) -> IcpProfileOut:
    """Save a workspace's outreach settings for a Lead Intel ICP. Writes `outreach.eo_icp_profiles` only."""
    if not await profiles.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    if not await lead_intel.get_icp(icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")
    # A chosen prompt must be this workspace's, and of the right kind.
    for kind, prompt_id in (("verify", body.verify_prompt_id), ("hook", body.hook_prompt_id), ("email", body.email_prompt_id)):
        if prompt_id is None:
            continue
        prompt = await prompts.get(workspace_id, prompt_id)
        if prompt is None or prompt.kind != kind:
            raise HTTPException(status_code=422, detail=f"That {kind} prompt isn't one of this workspace's")
    if body.product_id and not await products.get(workspace_id, body.product_id):
        raise HTTPException(status_code=422, detail="That product isn't one of this workspace's")
    profile = await profiles.upsert(workspace_id, icp_id, body.model_dump())
    return IcpProfileOut.model_validate(profile)
