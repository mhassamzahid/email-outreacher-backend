"""The do-not-contact list, ported from Lead Intel's exclusions. One list for every workspace.

Anything on it is never added from Lead Intel, Apollo or Google Maps, never made a lead, and never
written to. A company is matched by its place id, domain, phone, or name and city; a person by their
email or its domain. Everyone signed in can see the list; Management and admins change it.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import CompanyRepo, ExclusionRepo, exclusion_note
from dependencies import get_company_repo, get_exclusion_repo, require_manager, require_workspace_id
from schemas import ExcludeCompanyIn, ExclusionIn, ExclusionOut
from services.auth import CurrentUser
from services.identity import identity_keys, normalize_key

router = APIRouter(prefix="/exclusions", tags=["exclusions"])

HOW_TO_WRITE = {
    "domain": "a website, like inkco.com",
    "email": "an email address, like owner@inkco.com",
    "phone": "a phone number, like (312) 555-0100",
    "place_id": "a Google place id",
    "name_location": "a name and city",
}


@router.get("", response_model=list[ExclusionOut])
async def list_exclusions(exclusions: ExclusionRepo = Depends(get_exclusion_repo)) -> list[ExclusionOut]:
    return [ExclusionOut.model_validate(e) for e in await exclusions.list()]


@router.post("", response_model=ExclusionOut, status_code=201)
async def add_exclusion(
    body: ExclusionIn, user: CurrentUser = Depends(require_manager), exclusions: ExclusionRepo = Depends(get_exclusion_repo)
) -> ExclusionOut:
    value = normalize_key(body.key_type, body.value)
    if not value:
        raise HTTPException(status_code=422, detail=f"That isn't {HOW_TO_WRITE[body.key_type]}.")
    entry = {"key_type": body.key_type, "key_value": value, "reason": body.reason, "business_name": body.business_name.strip(), "note": body.note.strip()}
    added = await exclusions.add([entry], user.id)
    if not added:
        raise HTTPException(status_code=409, detail=f"{value} is already on the list.")
    return ExclusionOut.model_validate(added[0])


@router.delete("/{exclusion_id}", status_code=204, response_class=Response)
async def remove_exclusion(
    exclusion_id: UUID, _: CurrentUser = Depends(require_manager), exclusions: ExclusionRepo = Depends(get_exclusion_repo)
) -> Response:
    row = await exclusions.get(exclusion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not on the list")
    await exclusions.delete(row)
    return Response(status_code=204)


@router.post("/company/{company_id}", response_model=list[ExclusionOut], status_code=201)
async def exclude_company(
    company_id: UUID,
    body: ExcludeCompanyIn,
    workspace_id: UUID = Depends(require_workspace_id),
    user: CurrentUser = Depends(require_manager),
    exclusions: ExclusionRepo = Depends(get_exclusion_repo),
    companies: CompanyRepo = Depends(get_company_repo),
) -> list[ExclusionOut]:
    """Put a pipeline company on the list by every key it has, and mark it not a fit here."""
    company = await companies.get(workspace_id, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    keys = identity_keys(place_id=company.google_place_id, domain=company.domain, phone=company.phone,
                         name=company.name, city=company.city, state=company.state)  # fmt: skip
    if not keys:
        raise HTTPException(status_code=422, detail="This company has no website, phone or location to recognise it by.")
    added = await exclusions.add(
        [{"key_type": t, "key_value": v, "reason": body.reason, "business_name": company.name, "note": body.note.strip()} for t, v in keys],
        user.id,
    )
    hit = (await exclusions.matcher())(keys)
    company.status, company.overridden = "not_fit", True
    company.status_reason = exclusion_note(hit) if hit else "On the do-not-contact list"
    await companies.save(company)
    return [ExclusionOut.model_validate(e) for e in added]
