import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from crud import LeadRepo
from dependencies import get_lead_intel_repo, get_lead_repo, require_workspace_id
from lead_intel import LeadIntelRepo
from schemas import ColumnMapping, ImportResult, LeadIn, LeadOut, Page, RowOutcome
from services.leads import (
    TEMPLATE_ROWS,
    CsvError,
    Existing,
    clean_lead,
    duplicate_reason,
    parse_csv,
    remember,
    rows_to_leads,
    validate,
)


router = APIRouter(prefix="/leads", tags=["leads"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
LEAD_STATUSES = ("imported", "enriching", "enriched", "no_content", "generated", "approved", "sent", "skipped", "failed")


async def _check_targets(workspace_id: UUID, icp_id: UUID | None, leads: LeadRepo, lead_intel: LeadIntelRepo) -> None:
    if not await leads.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    if icp_id and not await lead_intel.get_icp(icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")


@router.get("/template.csv", response_class=Response)
def download_template() -> Response:
    """The CSV layout the import reads best: one header row, one example lead."""
    out = io.StringIO()
    csv.writer(out).writerows(TEMPLATE_ROWS)
    return Response(
        content=out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads-template.csv"'},
    )


@router.get("", response_model=Page[LeadOut])
async def list_leads(
    workspace_id: UUID = Depends(require_workspace_id),
    icp_id: UUID | None = Query(default=None, alias="icpId"),
    status: str | None = Query(default=None, pattern=f"^({'|'.join(LEAD_STATUSES)})$"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    leads: LeadRepo = Depends(get_lead_repo),
) -> Page[LeadOut]:
    rows, total = await leads.list(workspace_id, icp_id, status, q, limit, offset)
    return Page[LeadOut](items=[LeadOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=LeadOut, status_code=201)
async def add_lead(
    body: LeadIn,
    workspace_id: UUID = Depends(require_workspace_id),
    leads: LeadRepo = Depends(get_lead_repo),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
) -> LeadOut:
    """Add one lead by hand. 409 if the workspace already has them."""
    await _check_targets(workspace_id, body.icp_id, leads, lead_intel)
    lead = clean_lead(body.model_dump(), body.extra)
    if problem := validate(lead):
        raise HTTPException(status_code=422, detail=problem)
    if reason := duplicate_reason(lead, await leads.find_existing(workspace_id, [lead])):
        raise HTTPException(status_code=409, detail=f"Already a lead in this workspace ({reason})")
    [lead_id] = await leads.insert_many(workspace_id, body.icp_id, "manual", [lead])
    if lead_id is None:
        raise HTTPException(status_code=409, detail="Already a lead in this workspace")
    return LeadOut.model_validate(await leads.get(workspace_id, lead_id))


@router.post("/import", response_model=ImportResult)
async def import_csv(
    request: Request,
    workspace_id: UUID = Depends(require_workspace_id),
    icp_id: UUID | None = Query(default=None, alias="icpId"),
    dry_run: bool = Query(default=False, alias="dryRun"),
    leads: LeadRepo = Depends(get_lead_repo),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
) -> ImportResult:
    """Import leads from a CSV sent as the request body (Content-Type: text/csv).

    Every row gets an outcome: imported, duplicate (in the file or already in the workspace) or
    invalid. With dryRun=true nothing is saved, so the result can be shown as a preview first.
    """
    await _check_targets(workspace_id, icp_id, leads, lead_intel)
    data = await request.body()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file is over 5 MB. Split it and import the parts.")
    try:
        headers, rows = parse_csv(data)
    except CsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    mapping, candidates = rows_to_leads(headers, rows)
    existing = await leads.find_existing(workspace_id, candidates)
    seen = Existing()
    outcomes: list[RowOutcome] = []
    to_insert = []
    counts = {"in_file": 0, "existing": 0, "invalid": 0}

    for index, lead in enumerate(candidates):
        row = index + 2
        if problem := validate(lead):
            counts["invalid"] += 1
            outcomes.append(RowOutcome(row=row, outcome="invalid", reason=problem))
        elif reason := duplicate_reason(lead, existing):
            counts["existing"] += 1
            outcomes.append(RowOutcome(row=row, outcome="duplicate", reason=f"Already in this workspace ({reason})"))
        elif reason := duplicate_reason(lead, seen):
            counts["in_file"] += 1
            outcomes.append(RowOutcome(row=row, outcome="duplicate", reason=f"Repeated in this file ({reason})"))
        else:
            remember(lead, seen)
            outcomes.append(RowOutcome(row=row, outcome="imported"))
            to_insert.append((len(outcomes) - 1, lead))

    if not dry_run and to_insert:
        ids = await leads.insert_many(workspace_id, icp_id, "csv", [lead for _, lead in to_insert])
        for (position, _), lead_id in zip(to_insert, ids, strict=True):
            if lead_id is None:
                # Added by someone else since the check above; the database kept the first one.
                counts["existing"] += 1
                outcomes[position] = RowOutcome(
                    row=outcomes[position].row, outcome="duplicate", reason="Already in this workspace"
                )
            else:
                outcomes[position].lead_id = lead_id

    return ImportResult(
        dry_run=dry_run,
        total_rows=len(candidates),
        imported=sum(o.outcome == "imported" for o in outcomes),
        duplicates_in_file=counts["in_file"],
        duplicates_existing=counts["existing"],
        invalid=counts["invalid"],
        websites_from_email=sum(lead.website_derived for _, lead in to_insert),
        columns=[ColumnMapping(column=h, field=f) for h, f in zip(headers, mapping, strict=True)],
        rows=outcomes,
    )
