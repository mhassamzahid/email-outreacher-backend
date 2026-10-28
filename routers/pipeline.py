"""The company pipeline for an ICP: find companies, research and score them, find their stakeholders.

  find        Lead Intel companies matched to the ICP (already verified: qualified straight away)
              and Apollo companies matching its search terms, places and size. Deduplicated.
  research    Apollo companies: website + Instagram read, ICP rule checked, Claude score.
  status      keep or drop a company by hand, whatever the research said.
  stakeholders  people at qualified companies, through Apollo or LinkedIn (Apify).

Lead Intel is only read here.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from config import get_settings
from crud import CompanyRepo, IcpProfileRepo, ProviderCallRepo, StakeholderRepo
from dependencies import (
    get_company_repo,
    get_icp_profile_repo,
    get_lead_intel_repo,
    get_optional_apify,
    get_optional_apollo,
    get_optional_scorer,
    get_provider_call_repo,
    get_stakeholder_repo,
    get_website_reader,
    require_workspace_id,
)
from lead_intel import LeadIntelRepo
from schemas import (
    CompanySearchResult,
    CompanyStatusIn,
    FindCompaniesIn,
    FindCompaniesResult,
    PipelineCompanyOut,
    PipelineStakeholdersIn,
    ResearchIn,
    ResearchResult,
    SourceFindResult,
    StakeholderSearchResult,
)
from services.apify import EMPLOYEES_ACTOR, INSTAGRAM_PROFILE_ACTOR, ApifyClient, ApifyError
from services.apollo import SENIORITIES, ApolloClient, ApolloError
from services.research import research_companies
from services.scoring import CompanyScorer
from services.website import WebsiteReader, normalize_domain

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

LEAD_INTEL_MAX = 2000
STOP_STATUSES = {401, 402, 403, 429}


async def _icp(lead_intel: LeadIntelRepo, icp_id: UUID) -> dict[str, Any]:
    icp = await lead_intel.get_icp(icp_id)
    if not icp:
        raise HTTPException(status_code=404, detail="ICP not found")
    return icp


async def _workspace(companies: CompanyRepo, workspace_id: UUID) -> None:
    if not await companies.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.get("/companies", response_model=list[PipelineCompanyOut])
async def list_companies(
    icp_id: UUID | None = Query(default=None, alias="icpId"),
    status: str | None = Query(default=None, pattern="^(found|qualified|not_fit|error)$"),
    workspace_id: UUID = Depends(require_workspace_id),
    companies: CompanyRepo = Depends(get_company_repo),
) -> list[PipelineCompanyOut]:
    return [PipelineCompanyOut.model_validate(c) for c in await companies.list(workspace_id, icp_id, status)]


@router.post("/find", response_model=FindCompaniesResult)
async def find_companies(
    body: FindCompaniesIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    companies: CompanyRepo = Depends(get_company_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apollo: ApolloClient | None = Depends(get_optional_apollo),
) -> FindCompaniesResult:
    """Find companies for an ICP in Lead Intel and/or Apollo, and add the new ones to the pipeline."""
    await _workspace(companies, workspace_id)
    icp = await _icp(lead_intel, body.icp_id)
    li_result = apollo_result = None

    if "lead_intel" in body.sources:
        rows: list[dict[str, Any]] = []
        picked = list(dict.fromkeys(body.lead_intel_ids)) if body.lead_intel_ids else None
        while len(rows) < LEAD_INTEL_MAX:
            page, total = await lead_intel.list_companies(body.icp_id, None, 200, len(rows), only_ids=picked)
            rows += page
            if not page or len(rows) >= total:
                break
        found = [
            {
                "id": r["id"], "name": r["display_name"], "domain": normalize_domain(r.get("domain")),
                "website_url": r["fields"].get("website_url"), "instagram_url": r["fields"].get("instagram_url"),
                "city": r.get("city"), "state": r.get("state"),
            }
            for r in rows
        ]  # fmt: skip
        added, upgraded, had = await companies.add_lead_intel(workspace_id, body.icp_id, found)
        li_result = SourceFindResult(found=len(found), added=added, upgraded=upgraded, duplicates=had, total_available=len(rows))

    if "apollo" in body.sources:
        if apollo is None:
            apollo_result = SourceFindResult(error="APOLLO_API_KEY is not set")
        else:
            keywords = [k.strip() for k in body.apollo.keywords if k.strip()] or (icp.get("search_terms") or icp.get("any_of") or [])
            locations = [p.strip() for p in body.apollo.locations if p.strip()] or (icp.get("places") or [])
            try:
                orgs, total = await apollo.search_organizations(
                    keywords=keywords,
                    locations=locations,
                    employee_ranges=body.apollo.employee_ranges,
                    page=body.apollo.page,
                    per_page=body.apollo.per_page,
                )
            except ApolloError as exc:
                await calls.record(workspace_id, "apollo", "mixed_companies/search", "failed", action="Find companies")
                apollo_result = SourceFindResult(error=exc.message)
            else:
                await calls.record(workspace_id, "apollo", "mixed_companies/search", "succeeded", credits=1, action="Find companies")
                for org in orgs:
                    org.domain = normalize_domain(org.domain or org.website_url)
                added, dupes = await companies.add_apollo(workspace_id, body.icp_id, orgs)
                apollo_result = SourceFindResult(found=len(orgs), added=added, duplicates=dupes, total_available=total)

    return FindCompaniesResult(lead_intel=li_result, apollo=apollo_result)


@router.post("/research", response_model=ResearchResult)
async def research(
    body: ResearchIn,
    workspace_id: UUID = Depends(require_workspace_id),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    companies: CompanyRepo = Depends(get_company_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    reader: WebsiteReader = Depends(get_website_reader),
    apify: ApifyClient | None = Depends(get_optional_apify),
    scorer: CompanyScorer | None = Depends(get_optional_scorer),
) -> ResearchResult:
    """Research Apollo companies against their ICP and decide: qualified or not a fit.

    Lead Intel companies are skipped: Lead Intel already verified them. Up to 10 at a time; a
    website read takes up to 30 seconds (they're read three at a time).
    """
    rows = await companies.get_many(workspace_id, list(dict.fromkeys(body.company_ids)))
    todo = [c for c in rows if c.source == "apollo" and (body.force or c.researched_at is None)]
    skipped = len(body.company_ids) - len(todo)
    if not todo:
        return ResearchResult(researched=0, skipped=skipped, qualified=0, not_fit=0, errors=0, cost_usd=0, notes=[])

    settings = get_settings()
    summary_total = ResearchResult(researched=0, skipped=skipped, qualified=0, not_fit=0, errors=0, cost_usd=0, notes=[])
    by_icp: dict[UUID | None, list] = {}
    for c in todo:
        by_icp.setdefault(c.icp_id, []).append(c)
    for icp_id, group in by_icp.items():
        if icp_id is None:
            continue
        icp = await _icp(lead_intel, icp_id)
        profile_rows = await profiles.get_many(workspace_id, [icp_id])
        profile = profile_rows.get(icp_id)
        profile_dict = None if profile is None else {"description": profile.description, "pains": profile.pains, "offer": profile.offer}
        summary = await research_companies(
            group, icp, profile_dict, reader, apify, scorer, settings.company_score_threshold
        )
        for company in group:
            await companies.save(company)
        if summary.apify_cost_usd:
            await calls.record(
                workspace_id, "apify", INSTAGRAM_PROFILE_ACTOR, "succeeded", cost_usd=summary.apify_cost_usd, action="Research companies"
            )
        if summary.claude_calls:
            cost = (summary.input_tokens * settings.claude_input_usd_per_mtok + summary.output_tokens * settings.claude_output_usd_per_mtok) / 1e6
            await calls.record(
                workspace_id, "claude", "messages", "succeeded", cost_usd=round(cost, 4), action="Research companies",
                model=summary.claude_model, input_tokens=summary.input_tokens, output_tokens=summary.output_tokens,
            )  # fmt: skip
        summary_total.researched += len(group)
        summary_total.qualified += summary.qualified
        summary_total.not_fit += summary.not_fit
        summary_total.errors += summary.errors
        summary_total.cost_usd = round(summary_total.cost_usd + summary.apify_cost_usd, 4)
        summary_total.notes += summary.notes
    return summary_total


@router.patch("/companies/{company_id}", response_model=PipelineCompanyOut)
async def set_status(
    company_id: UUID,
    body: CompanyStatusIn,
    workspace_id: UUID = Depends(require_workspace_id),
    companies: CompanyRepo = Depends(get_company_repo),
) -> PipelineCompanyOut:
    """Keep or drop a company by hand. Research won't change it back."""
    company = await companies.get(workspace_id, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.status = body.status
    company.status_reason = "Kept by hand" if body.status == "qualified" else "Dropped by hand"
    company.overridden = True
    await companies.save(company)
    return PipelineCompanyOut.model_validate(company)


@router.post("/stakeholders", response_model=StakeholderSearchResult)
async def find_stakeholders(
    body: PipelineStakeholdersIn,
    workspace_id: UUID = Depends(require_workspace_id),
    companies: CompanyRepo = Depends(get_company_repo),
    stakeholders: StakeholderRepo = Depends(get_stakeholder_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    apollo: ApolloClient | None = Depends(get_optional_apollo),
    apify: ApifyClient | None = Depends(get_optional_apify),
) -> StakeholderSearchResult:
    """Find people at qualified companies: Apollo by website domain (free search), or LinkedIn
    through Apify by company name (paid per person). Only qualified companies are searched."""
    if bad := [s for s in body.seniorities if s not in SENIORITIES]:
        raise HTTPException(status_code=422, detail=f"Unknown seniorities: {', '.join(bad)}")
    client = apollo if body.source == "apollo" else apify
    if client is None:
        missing = "APOLLO_API_KEY" if body.source == "apollo" else "APIFY_TOKEN"
        raise HTTPException(status_code=503, detail=f"{missing} is not set")
    if body.source == "linkedin" and len(body.company_ids) > 10:
        raise HTTPException(status_code=422, detail="Search LinkedIn for 10 companies or fewer at a time")

    rows = {c.id: c for c in await companies.get_many(workspace_id, list(dict.fromkeys(body.company_ids)))}
    titles = [t.strip() for t in body.titles if t.strip()]
    results: list[CompanySearchResult] = []
    error: str | None = None
    cost = 0.0
    for cid in dict.fromkeys(body.company_ids):
        c = rows.get(cid)
        name = c.name if c else None
        if c is None:
            results.append(CompanySearchResult(company_id=cid, name=None, status="skipped", reason="Not in this workspace"))
            continue
        if error:
            results.append(CompanySearchResult(company_id=cid, name=name, status="failed", reason="Not searched"))
            continue
        if c.status != "qualified":
            results.append(CompanySearchResult(company_id=cid, name=name, status="skipped", reason="Not qualified yet"))
            continue
        try:
            if body.source == "apollo":
                if not c.domain:
                    results.append(CompanySearchResult(company_id=cid, name=name, status="skipped", reason="No website domain to search by"))
                    continue
                people, total = await apollo.search_people(c.domain, titles, body.seniorities)  # type: ignore[union-attr]
                await calls.record(workspace_id, "apollo", "mixed_people/api_search", "succeeded", credits=0, action="Find stakeholders")
                for p in people:
                    p.organization_name = p.organization_name or c.name
                new = await stakeholders.save_found(
                    workspace_id, c.lead_intel_business_id, people, icp_id=c.icp_id, pipeline_company_id=c.id
                )
                results.append(CompanySearchResult(company_id=cid, name=name, status="searched", found=len(people), new=new, total_at_apollo=total))
            else:
                people, run = await apify.company_employees(c.linkedin_url or c.name, titles, body.max_per_company)  # type: ignore[union-attr]
                cost += run.cost_usd or 0
                await calls.record(workspace_id, "apify", EMPLOYEES_ACTOR, "succeeded", cost_usd=run.cost_usd, action="Find stakeholders")
                new = await stakeholders.save_employees(workspace_id, c.lead_intel_business_id, people, pipeline_company_id=c.id)
                results.append(CompanySearchResult(company_id=cid, name=name, status="searched", found=len(people), new=new))
        except (ApolloError, ApifyError) as exc:
            provider = "apollo" if body.source == "apollo" else "apify"
            await calls.record(workspace_id, provider, "people search", "failed", action="Find stakeholders")
            results.append(CompanySearchResult(company_id=cid, name=name, status="failed", reason=exc.message))
            if exc.status in STOP_STATUSES:
                error = exc.message
    return StakeholderSearchResult(results=results, error=error, cost_usd=round(cost, 4) or None)
