"""Sending through CheapSequencer: settings, campaigns, queueing approved emails, and follow-ups.

Nothing here activates a campaign or sends: emails wait in a campaign until a person activates it in
CheapSequencer. See services/outreach.py for how queueing and follow-ups work.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from crud import (
    ExampleRepo, ExclusionRepo, IcpProfileRepo, ProductRepo, PromptRepo, ProviderCallRepo, SendingRepo, SettingsRepo,
)  # fmt: skip
from dependencies import (
    get_example_repo, get_exclusion_repo, get_icp_profile_repo, get_lead_intel_repo, get_optional_sequencer,
    get_optional_writer, get_product_repo, get_prompt_repo, get_provider_call_repo, get_sending_repo, get_settings_repo,
    require_manager, require_workspace_id,
)  # fmt: skip
from lead_intel import LeadIntelRepo
from schemas import (
    FollowUpRunOut, QueueIn, QueueOut, QueueOutcomeOut, SendingCampaignOut, SendingSettingsIn, SendingSettingsOut, TemplateOutcomeOut,
)  # fmt: skip
from services import outreach
from services.auth import CurrentUser
from services.generation import EmailWriter
from services.sending import SendingError, SequencerClient

router = APIRouter(prefix="/sending", tags=["sending"])


def _sequencer(sequencer: SequencerClient | None) -> SequencerClient:
    if sequencer is None:
        raise HTTPException(status_code=503, detail="CHEAPSEQUENCER_API_KEY is not set")
    return sequencer


async def _deps(
    workspace_id: UUID = Depends(require_workspace_id),
    sending: SendingRepo = Depends(get_sending_repo),
    settings: SettingsRepo = Depends(get_settings_repo),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
    prompts: PromptRepo = Depends(get_prompt_repo),
    products: ProductRepo = Depends(get_product_repo),
    library: ExampleRepo = Depends(get_example_repo),
    exclusions: ExclusionRepo = Depends(get_exclusion_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    lead_intel: LeadIntelRepo = Depends(get_lead_intel_repo),
    sequencer: SequencerClient | None = Depends(get_optional_sequencer),
    writer: EmailWriter | None = Depends(get_optional_writer),
) -> outreach.Deps:
    return outreach.Deps(workspace_id, sending, settings, profiles, prompts, products, library, exclusions, calls,
                         lead_intel, _sequencer(sequencer), writer)  # fmt: skip


@router.get("/settings", response_model=SendingSettingsOut)
async def get_settings_(workspace_id: UUID = Depends(require_workspace_id), settings: SettingsRepo = Depends(get_settings_repo)):
    return SendingSettingsOut(**await settings.sending(workspace_id), templates=[])


@router.put("/settings", response_model=SendingSettingsOut)
async def save_settings(
    body: SendingSettingsIn,
    workspace_id: UUID = Depends(require_workspace_id),
    settings: SettingsRepo = Depends(get_settings_repo),
    deps: outreach.Deps = Depends(_deps),
    _: CurrentUser = Depends(require_manager),
) -> SendingSettingsOut:
    """Save, then write the signature into every Coldbrew campaign that can still be changed."""
    values = body.model_dump()
    values["sender_company"] = values["sender_company"].strip()
    values["sender_address"] = "\n".join(ln.strip() for ln in values["sender_address"].strip().splitlines() if ln.strip())
    saved = await settings.save_sending(workspace_id, values)
    try:
        templates = await outreach.sync_templates(deps)
    except SendingError as exc:
        templates = [outreach.TemplateOutcome("CheapSequencer", "skipped", exc.message)]
    return SendingSettingsOut(**saved, templates=[TemplateOutcomeOut(**vars(t)) for t in templates])


@router.get("/campaigns", response_model=list[SendingCampaignOut])
async def campaigns(deps: outreach.Deps = Depends(_deps)) -> list[SendingCampaignOut]:
    """Coldbrew's campaigns in CheapSequencer, with how many leads Coldbrew queued into each."""
    try:
        rows = await deps.sequencer.campaigns()
    except SendingError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    counts = await deps.sending.queued_counts(deps.workspace_id)
    return [
        SendingCampaignOut(id=c["id"], name=c.get("name") or "", status=c.get("status") or "", daily_send_cap=c.get("daily_send_cap"),
                           queued=counts.get(c["id"], 0))  # fmt: skip
        for c in rows
        if (c.get("name") or "").startswith(outreach.CAMPAIGN_PREFIX)
    ]


@router.post("/queue", response_model=QueueOut)
async def queue(body: QueueIn, deps: outreach.Deps = Depends(_deps)) -> QueueOut:
    """Put approved first emails into their ICP's campaign. The campaign isn't activated: nothing sends
    until a person activates it in CheapSequencer."""
    try:
        outcomes = await outreach.queue(deps, list(dict.fromkeys(body.email_ids)))
    except SendingError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return QueueOut(
        queued=sum(o.status == "queued" for o in outcomes),
        results=[QueueOutcomeOut(email_id=o.email_id, status=o.status, reason=o.reason, campaign=o.campaign) for o in outcomes],
    )


@router.post("/follow-ups/run", response_model=FollowUpRunOut)
async def run_follow_ups(deps: outreach.Deps = Depends(_deps)) -> FollowUpRunOut:
    """Check every lead's place in its campaign now and write the follow-ups that are nearly due.
    This also runs on its own every 30 minutes."""
    result = await outreach.run_follow_ups(deps)
    return FollowUpRunOut(checked=result.checked, written=result.written, ended=result.ended,
                          cost_usd=round(result.cost_usd, 4), notes=result.notes)  # fmt: skip

