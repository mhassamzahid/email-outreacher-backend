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
    get_apify,
    get_apollo,
    get_lead_intel_repo,
    get_provider_call_repo,
    get_stakeholder_repo,
    get_workspace_company_repo,
    require_workspace_id,
)
from lead_intel import LeadIntelRepo
from schemas import (
    AddAsLeadsIn,
    AddAsLeadsResult,
    CompanySearchResult,
    LinkedInSearchIn,
    IcpSearchIn,
    IcpSearchResult,
    RevealOutcome,
    StakeholderOut,
    StakeholderRevealIn,
    StakeholderRevealResult,
    StakeholderSearchIn,
    StakeholderSearchResult,
)
from services.apify import EMPLOYEES_ACTOR, ApifyClient, ApifyError
from services.apollo import SENIORITIES, ApolloClient, ApolloError
from services.leads import clean_lead

router = APIRouter(prefix="/stakeholders", tags=["stakeholders"])

# Refusals that will repeat for every other company too, so there's no point carrying on.
STOP_STATUSES = {401, 403, 429}


@router.get("", response_model=list[StakeholderOut])
async def list_stakeholders(
    company_id: UUID | None = Query(default=None, alias="companyId"),
    icp_id: UUID | None = Query(default=None, alias="icpId"),
    workspace_id: UUID = Depends(require_workspace_id),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
) -> list[StakeholderOut]:
    return [StakeholderOut.model_validate(s) for s in await stakeholders.list(workspace_id, company_id, icp_id)]


@router.post("/search-linkedin", response_model=StakeholderSearchResult)
async def search_linkedin(
    body: LinkedInSearchIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apify: ApifyClient = Depends(get_apify),
) -> StakeholderSearchResult:
    """Find people at each company on LinkedIn, through Apify. Full names and LinkedIn URLs, no emails.

    Paid per profile found (about $0.004 each). Companies are looked up on LinkedIn by name, since
    Lead Intel has no LinkedIn company URL; check the results look like the right company.
    """
    if not await workspace_companies.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    in_workspace = await workspace_companies.imported(workspace_id)
    ids = list(dict.fromkeys(body.company_ids))
    companies, _ = await lead_intel.list_companies(None, None, len(ids), 0, only_ids=ids)
    by_id = {c["id"]: c for c in companies}
    titles = [t.strip() for t in body.titles if t.strip()]

    results: list[CompanySearchResult] = []
    error: str | None = None
    total_cost = 0.0
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
        try:
            people, run = await apify.company_employees(company["display_name"], titles, body.max_per_company)
        except ApifyError as exc:
            await calls.record(workspace_id, "apify", EMPLOYEES_ACTOR, "failed")
            results.append(CompanySearchResult(company_id=company_id, name=name, status="failed", reason=exc.message))
            if exc.status in STOP_STATUSES | {402}:
                error = exc.message
            continue
        total_cost += run.cost_usd or 0
        await calls.record(
            workspace_id, "apify", EMPLOYEES_ACTOR, "succeeded",
            key=f"apify:run:{run.run_id}" if run.run_id else None, cost_usd=run.cost_usd,
        )  # fmt: skip
        new = await stakeholders.save_employees(workspace_id, company_id, people)
        results.append(CompanySearchResult(company_id=company_id, name=name, status="searched", found=len(people), new=new))
    return StakeholderSearchResult(results=results, error=error, cost_usd=round(total_cost, 4))


@router.post("/add-as-leads", response_model=AddAsLeadsResult)
async def add_as_leads(
    body: AddAsLeadsIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    session: AsyncSession = Depends(get_session),
) -> AddAsLeadsResult:
    """Make leads from people you already have a LinkedIn URL or an email for. Free: no provider calls.

    A lead with only a LinkedIn URL can have its posts fetched now and its email revealed later.
    """
    ids = list(dict.fromkeys(body.stakeholder_ids))
    found = await stakeholders.get_many(workspace_id, ids)
    company_ids = list({s.lead_intel_business_id for s in found if s.lead_intel_business_id})
    companies, _ = await lead_intel.list_companies(None, None, max(len(company_ids), 1), 0, only_ids=company_ids)
    by_company = {c["id"]: c for c in companies}
    added = linked = skipped = 0
    for s in found:
        # Unrevealed Apollo people only have a masked name and no contact; reveal them instead.
        if s.lead_id or not (s.linkedin_url or s.email) or (s.source == "apollo" and not s.revealed_at):
            skipped += 1
            continue
        company = by_company.get(s.lead_intel_business_id) or {}
        lead = clean_lead(
            {
                "first_name": s.first_name,
                "last_name": s.last_name,
                "email": s.email or "",
                "title": s.title,
                "company": company.get("display_name") or s.organization_name or "",
                "website": company.get("domain") or "",
                "linkedin_url": s.linkedin_url or "",
            }
        )
        lead_id, created = await lead_from_stakeholder(
            session, workspace_id, company.get("icp_id") or s.lead_intel_icp_id, s.lead_intel_business_id, lead,
            s.external_id, s.email_status, origin="apify" if s.source == "apify_linkedin" else "apollo",
        )  # fmt: skip
        if lead_id is None:
            skipped += 1
            continue
        await stakeholders.link_lead(s, lead_id)
        added += created
        linked += not created
    skipped += len(ids) - len(found)
    return AddAsLeadsResult(added=added, linked_existing=linked, skipped=skipped)


@router.post("/search-icp", response_model=IcpSearchResult)
async def search_by_icp(
    body: IcpSearchIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    workspace_companies: WorkspaceCompanyRepo = Depends(get_workspace_company_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apollo: ApolloClient = Depends(get_apollo),
) -> IcpSearchResult:
    """Search Apollo for people who fit an ICP: its titles, places, size and keywords. Uses no credits.

    One page of results per call; ask for the next page to see more. Lead Intel is only read, to
    check the ICP exists.
    """
    if bad := [s for s in body.seniorities if s not in SENIORITIES]:
        raise HTTPException(status_code=422, detail=f"Unknown seniorities: {', '.join(bad)}")
    if bad := [r for r in body.employee_ranges if not _is_range(r)]:
        raise HTTPException(status_code=422, detail=f"Employee ranges must look like 1,10: {', '.join(bad)}")
    if not await workspace_companies.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    if not await lead_intel.get_icp(body.icp_id):
        raise HTTPException(status_code=404, detail="ICP not found")
    titles = [t.strip() for t in body.titles if t.strip()]
    locations = [loc.strip() for loc in body.locations if loc.strip()]
    if not (titles or body.seniorities or locations or body.keywords.strip() or body.employee_ranges):
        raise HTTPException(status_code=422, detail="Add at least one filter, or the search would match everyone")

    try:
        people, total = await apollo.search_people(
            None,
            titles,
            body.seniorities,
            body.per_page,
            page=body.page,
            organization_locations=locations,
            keywords=body.keywords.strip() or None,
            employee_ranges=body.employee_ranges,
            email_statuses=["verified"] if body.verified_email_only else None,
        )
    except ApolloError as exc:
        await calls.record(workspace_id, "apollo", "mixed_people/api_search", "failed")
        return IcpSearchResult(found=0, new=0, total_at_apollo=None, page=body.page, error=exc.message)
    await calls.record(workspace_id, "apollo", "mixed_people/api_search", "succeeded", credits=0)
    new = await stakeholders.save_found(workspace_id, None, people, icp_id=body.icp_id)
    return IcpSearchResult(found=len(people), new=new, total_at_apollo=total, page=body.page)


def _is_range(value: str) -> bool:
    parts = value.split(",")
    return len(parts) == 2 and all(p.strip().isdigit() for p in parts) and int(parts[0]) <= int(parts[1])


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
            # People found on LinkedIn have no Apollo id; Apollo matches them by LinkedIn URL.
            if s.source == "apify_linkedin":
                person = await apollo.reveal(linkedin_url=s.linkedin_url) if s.linkedin_url else None
            else:
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

        # People found by ICP have no Lead Intel company; Apollo's own company data stands in.
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
                session, workspace_id, company.get("icp_id") or s.lead_intel_icp_id, s.lead_intel_business_id, lead,
                s.external_id, person.email_status, origin="apify" if s.source == "apify_linkedin" else "apollo",
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
