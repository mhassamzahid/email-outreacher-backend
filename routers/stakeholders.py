"""Find the people at a workspace's companies (Apollo search, free) and reveal the ones worth emailing
(Apollo enrichment, paid). Revealing a person turns them into a lead.

Lead Intel is only read here: for each company's name, domain and ICP.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from crud import ProviderCallRepo, StakeholderRepo, WorkspaceCompanyRepo, lead_from_stakeholder
from database import get_session
from dependencies import (
    get_apollo,
    get_lead_intel_repo,
    get_provider_call_repo,
    get_stakeholder_repo,
    get_workspace_company_repo,
    require_workspace_id,
)
from lead_intel import LeadIntelRepo
from schemas import (
    CompanySearchResult,
    RevealOutcome,
    StakeholderOut,
    StakeholderRevealIn,
    StakeholderRevealResult,
    StakeholderSearchIn,
    StakeholderSearchResult,
)
from services.apollo import SENIORITIES, ApolloClient, ApolloError
from services.leads import clean_lead

router = APIRouter(prefix="/stakeholders", tags=["stakeholders"])

# Refusals that will repeat for every other company too, so there's no point carrying on.
STOP_STATUSES = {401, 403, 429}


@router.get("", response_model=list[StakeholderOut])
async def list_stakeholders(
    company_id: UUID | None = Query(default=None, alias="companyId"),
    workspace_id: UUID = Depends(require_workspace_id),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
) -> list[StakeholderOut]:
    return [StakeholderOut.model_validate(s) for s in await stakeholders.list(workspace_id, company_id)]


@router.post("/search", response_model=StakeholderSearchResult)
async def search(
    body: StakeholderSearchIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apollo: ApolloClient = Depends(get_apollo),
) -> StakeholderSearchResult:
    """Search Apollo for people at each company, by title and seniority. Uses no Apollo credits."""
    if bad := [s for s in body.seniorities if s not in SENIORITIES]:
        raise HTTPException(status_code=422, detail=f"Unknown seniorities: {', '.join(bad)}")
    if not await workspace_companies.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    in_workspace = await workspace_companies.imported(workspace_id)
    ids = list(dict.fromkeys(body.company_ids))
    companies, _ = await lead_intel.list_companies(None, None, len(ids), 0, only_ids=ids)
    by_id = {c["id"]: c for c in companies}
    titles = [t.strip() for t in body.titles if t.strip()]

    results: list[CompanySearchResult] = []
    error: str | None = None
    for company_id in ids:
        company = by_id.get(company_id)
        name = company["display_name"] if company else None
        if error:
            results.append(CompanySearchResult(company_id=company_id, name=name, status="failed", reason="Not searched"))
            continue
        if company_id not in in_workspace or not company:
            reason = "Not in this workspace" if company else "Not found in Lead Intel, or no longer contactable"
            results.append(CompanySearchResult(company_id=company_id, name=name, status="skipped", reason=reason))
            continue
        if not company["domain"]:
            results.append(
                CompanySearchResult(
                    company_id=company_id, name=name, status="skipped", reason="No website domain in Lead Intel to search by"
                )
            )
            continue
        try:
            people, total = await apollo.search_people(company["domain"], titles, body.seniorities)
        except ApolloError as exc:
            await calls.record(workspace_id, "apollo", "mixed_people/api_search", "failed")
            results.append(CompanySearchResult(company_id=company_id, name=name, status="failed", reason=exc.message))
            if exc.status in STOP_STATUSES:
                error = exc.message
            continue
        await calls.record(workspace_id, "apollo", "mixed_people/api_search", "succeeded", credits=0)
        new = await stakeholders.save_found(workspace_id, company_id, people)
        results.append(
            CompanySearchResult(
                company_id=company_id, name=name, status="searched", found=len(people), new=new, total_at_apollo=total
            )
        )
    return StakeholderSearchResult(results=results, error=error)


@router.post("/reveal", response_model=StakeholderRevealResult)
async def reveal(
    body: StakeholderRevealIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    session: AsyncSession = Depends(get_session),
    apollo: ApolloClient = Depends(get_apollo),
) -> StakeholderRevealResult:
    """Reveal each person's full name, work email and LinkedIn, and add them as a lead.

    Costs Apollo credits (about 1 per person with data; nothing when Apollo finds no match).
    People already revealed are skipped, so they're never paid for twice.
    """
    ids = list(dict.fromkeys(body.stakeholder_ids))
    found = {s.id: s for s in await stakeholders.get_many(workspace_id, ids)}
    company_ids = list({s.lead_intel_business_id for s in found.values() if s.lead_intel_business_id})
    companies, _ = await lead_intel.list_companies(None, None, max(len(company_ids), 1), 0, only_ids=company_ids)
    by_company = {c["id"]: c for c in companies}

    outcomes: list[RevealOutcome] = []
    error: str | None = None
    for sid in ids:
        s = found.get(sid)
        if s is None:
            outcomes.append(RevealOutcome(stakeholder_id=sid, status="not_found"))
            continue
        if s.revealed_at is not None:
            outcomes.append(
                RevealOutcome(stakeholder_id=sid, status="already_revealed", email=s.email, lead_id=s.lead_id)
            )
            continue
        if error:
            outcomes.append(RevealOutcome(stakeholder_id=sid, status="failed", reason="Not revealed"))
            continue
        try:
            person = await apollo.reveal(s.external_id)
        except ApolloError as exc:
            await calls.record(workspace_id, "apollo", "people/match", "failed")
            outcomes.append(RevealOutcome(stakeholder_id=sid, status="failed", reason=exc.message))
            if exc.status in STOP_STATUSES:
                error = exc.message
            continue

        if person is None:
            await calls.record(workspace_id, "apollo", "people/match", "succeeded", credits=0)
            await stakeholders.save_revealed(s, None, None)
            outcomes.append(RevealOutcome(stakeholder_id=sid, status="no_match", reason="Apollo had no match"))
            continue

        company = by_company.get(s.lead_intel_business_id) or {}
        lead = clean_lead(
            {
                "first_name": person.first_name,
                "last_name": person.last_name,
                "email": person.email or "",
                "title": person.title,
                "company": company.get("display_name") or person.organization_name or "",
                "website": company.get("domain") or person.organization_domain or "",
                "linkedin_url": person.linkedin_url or "",
            }
        )
        lead_id, created = (None, False)
        if lead.email or lead.linkedin_url:
            lead_id, created = await lead_from_stakeholder(
                session, workspace_id, company.get("icp_id"), s.lead_intel_business_id, lead, s.external_id,
                person.email_status,
            )  # fmt: skip
        await calls.record(
            workspace_id,
            "apollo",
            "people/match",
            "succeeded",
            key=f"apollo:people/match:{workspace_id}:{s.external_id}",
            lead_id=lead_id,
        )
        await stakeholders.save_revealed(s, person, lead_id)
        outcomes.append(
            RevealOutcome(
                stakeholder_id=sid, status="revealed", email=person.email, lead_id=lead_id, lead_created=created
            )
        )

    return StakeholderRevealResult(
        outcomes=outcomes, revealed=sum(o.status == "revealed" for o in outcomes), error=error
    )
