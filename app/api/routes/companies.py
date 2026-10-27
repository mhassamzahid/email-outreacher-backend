from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_lead_intel_repo
from app.repositories.lead_intel import LeadIntelRepo
from app.schemas.base import Page
from app.schemas.companies import Company, CompanyDetail, Instagram

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
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
) -> Page[Company]:
    """Contactable Lead Intel businesses: active and not excluded. Read-only."""
    rows, total = await lead_intel.list_companies(icp_id, q, limit, offset)
    return Page[Company](
        items=[Company.model_validate(_shape(row)) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/icps/{icp_id}/companies", response_model=Page[Company])
async def list_icp_companies(
    icp_id: UUID,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
) -> Page[Company]:
    return await list_companies(icp_id, q, limit, offset, lead_intel)


@router.get("/companies/{company_id}", response_model=CompanyDetail)
async def get_company(company_id: UUID, lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo)) -> CompanyDetail:
    row = await lead_intel.get_company(company_id)
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    return CompanyDetail.model_validate(_shape(row))
