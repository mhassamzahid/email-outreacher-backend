from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from crud import WorkspaceCompanyRepo
from dependencies import get_lead_intel_repo, get_workspace_company_repo, get_workspace_id, require_manager, require_workspace_id
from lead_intel import LeadIntelRepo
from schemas import (
    Company,
    CompanyDetail,
    CompanyImportIn,
    CompanyImportResult,
    CompanyRemoveIn,
    CompanyRemoveResult,
    Instagram,
    Page,
)


router = APIRouter(tags=["companies"])


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    fields = row["fields"]
    return {
        **row,
        "name": row["display_name"],
        "website_url": fields.get("website_url"),
        "instagram": Instagram(
            handle=fields.get("instagram_handle"),
            url=fields.get("instagram_url"),
            followers=_to_int(fields.get("instagram_followers")),
            category=fields.get("instagram_category"),
            bio=fields.get("instagram_bio"),
        ),
    }


@router.get("/companies", response_model=Page[Company])
async def list_companies(
    icp_id: UUID | None = Query(default=None, alias="icpId"),
    no_icp: bool = Query(default=False, alias="noIcp", description="Only companies no Lead Intel ICP matched"),
    in_workspace: bool | None = Query(
        default=None,
        alias="inWorkspace",
        description="true: only companies in the workspace; false: only ones not in it yet. Needs X-Workspace-Id.",
    ),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID | None = Depends(get_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
) -> Page[Company]:
    """Contactable Lead Intel businesses: active and not excluded. Lead Intel is only read.

    With X-Workspace-Id, each company says when it was imported into that workspace (or null).
    """
    if in_workspace is not None and workspace_id is None:
        raise HTTPException(status_code=400, detail="inWorkspace needs the X-Workspace-Id header")
    imported = await workspace_companies.imported(workspace_id) if workspace_id else {}
    rows, total = await lead_intel.list_companies(
        icp_id,
        q,
        limit,
        offset,
        no_icp=no_icp,
        only_ids=list(imported) if in_workspace is True else None,
        exclude_ids=list(imported) if in_workspace is False else None,
    )
    items = [Company.model_validate({**_shape(row), "imported_at": imported.get(row["id"])}) for row in rows]
    return Page[Company](items=items, total=total, limit=limit, offset=offset)


@router.get("/icps/{icp_id}/companies", response_model=Page[Company])
async def list_icp_companies(
    icp_id: UUID,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workspace_id: UUID | None = Depends(get_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
) -> Page[Company]:
    return await list_companies(icp_id, False, None, q, limit, offset, workspace_id, lead_intel, workspace_companies)


@router.post("/companies/import", response_model=CompanyImportResult, dependencies=[Depends(require_manager)])
async def import_companies(
    body: CompanyImportIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
) -> CompanyImportResult:
    """Pull Lead Intel companies into the workspace. Lead Intel is only read; only the link is saved here."""
    if (body.company_ids is None) == (body.icp_id is None):
        raise HTTPException(status_code=422, detail="Send either companyIds or icpId")
    if not await workspace_companies.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    if body.icp_id and not await lead_intel.get_icp(body.icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")

    wanted = list(dict.fromkeys(body.company_ids)) if body.company_ids is not None else None
    valid = await lead_intel.contactable_ids(ids=wanted, icp_id=body.icp_id)
    not_found = [cid for cid in wanted if cid not in set(valid)] if wanted is not None else []
    added = await workspace_companies.add(workspace_id, valid)
    return CompanyImportResult(imported=added, already_in_workspace=len(valid) - added, not_found=not_found)


@router.post("/companies/remove", response_model=CompanyRemoveResult, dependencies=[Depends(require_manager)])
async def remove_companies(
    body: CompanyRemoveIn,
    workspace_id: UUID = Depends(require_workspace_id),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
) -> CompanyRemoveResult:
    """Take companies out of the workspace. They stay in Lead Intel, untouched."""
    return CompanyRemoveResult(removed=await workspace_companies.remove(workspace_id, body.company_ids))


@router.get("/companies/{company_id}", response_model=CompanyDetail)
async def get_company(company_id: UUID, lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo)) -> CompanyDetail:
    row = await lead_intel.get_company(company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    return CompanyDetail.model_validate(_shape(row))
