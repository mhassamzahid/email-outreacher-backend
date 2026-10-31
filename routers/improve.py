"""Improving an ICP's Email Generation prompt from the feedback on its emails.

Claude proposes a revised prompt; nothing is saved here. The person reviews it and saves it as a new
version (POST /prompts/{id}/versions) if they want it.
"""

from uuid import UUID

import anthropic
from fastapi import APIRouter, Depends, HTTPException

from crud import FeedbackRepo, IcpProfileRepo, PromptRepo, ProviderCallRepo, SettingsRepo
from dependencies import (
    get_feedback_repo,
    get_icp_profile_repo,
    get_optional_anthropic,
    get_prompt_repo,
    get_provider_call_repo,
    get_settings_repo,
    require_manager,
    require_workspace_id,
)
from schemas import SuggestIn, SuggestOut
from services.claude import cost_usd
from services.generation import Example
from services.improve import suggest_prompt

router = APIRouter(prefix="/improve", tags=["improve"])

MIN_RATED = 3


@router.post("/email-prompt", response_model=SuggestOut, dependencies=[Depends(require_manager)])
async def suggest_email_prompt(
    body: SuggestIn,
    workspace_id: UUID = Depends(require_workspace_id),
    profiles: IcpProfileRepo = Depends(get_icp_profile_repo),
    prompts: PromptRepo = Depends(get_prompt_repo),
    feedback: FeedbackRepo = Depends(get_feedback_repo),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
    settings: SettingsRepo = Depends(get_settings_repo),
    client: anthropic.AsyncAnthropic | None = Depends(get_optional_anthropic),
) -> SuggestOut:
    """A revised version of the Email Generation prompt this ICP uses, from its rated emails."""
    if client is None:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not set")
    # Up to the workspace's count of rated emails, half good and half bad where there are enough of each.
    total = (await settings.feedback(workspace_id))["improve_examples"]
    pool = await feedback.examples(workspace_id, body.icp_id, total, total)
    good = [f for f in pool if f.rating == "good"]
    bad = [f for f in pool if f.rating == "bad"]
    n_bad = min(len(bad), max(total // 2, total - len(good)))
    rated = good[: total - n_bad] + bad[:n_bad]
    if len(rated) < MIN_RATED:
        raise HTTPException(status_code=422, detail=f"Rate at least {MIN_RATED} of this ICP's emails first")
    profile = (await profiles.get_many(workspace_id, [body.icp_id])).get(body.icp_id)
    version = await prompts.for_icp(workspace_id, "email", profile.email_prompt_id if profile else None)
    if version is None:
        raise HTTPException(status_code=404, detail="This ICP has no email prompt")
    prompt = await prompts.get(workspace_id, version.prompt_id)
    examples = [Example(f.rating, f.subject, f.body, f.hook, list(f.tags), f.note) for f in rated]
    s = await suggest_prompt(client, version.system_prompt, version.user_prompt, examples)
    cost = cost_usd(s.model, s.input_tokens, s.output_tokens)
    await calls.record(
        workspace_id, "claude", "messages", "failed" if s.result is None else "succeeded", cost_usd=cost,
        action="Improve prompt", model=s.model, input_tokens=s.input_tokens, output_tokens=s.output_tokens,
    )  # fmt: skip
    if s.result is None:
        raise HTTPException(status_code=502, detail=s.error)
    return SuggestOut(
        prompt_id=prompt.id,
        prompt_name=prompt.name,
        based_on_version=version.version,
        chosen_by_icps=await prompts.in_use(prompt),
        is_default=prompt.is_default,
        examples=len(examples),
        summary=s.result.summary,
        changes=s.result.changes,
        system_prompt=s.result.system_prompt,
        user_prompt=s.result.user_prompt,
        cost_usd=cost,
    )
