"""What the workspace has spent, and on what: Claude tokens, Apollo credits, Apify dollars.

Read from the call ledger (`eo_provider_calls`), which every paid call writes to.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from crud import ProviderCallRepo
from dependencies import get_provider_call_repo, require_workspace_id
from schemas import ActionUsage, ProviderCallOut, ProviderUsage, UsageOut

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageOut)
async def usage(
    days: int = Query(default=30, ge=1, le=365),
    workspace_id: UUID = Depends(require_workspace_id),
    calls: ProviderCallRepo = Depends(get_provider_call_repo),
) -> UsageOut:
    since = datetime.now(UTC) - timedelta(days=days)
    data = await calls.usage(workspace_id, since)
    return UsageOut(
        since=since,
        providers=[ProviderUsage.model_validate(r) for r in data["providers"]],
        actions=[ActionUsage.model_validate(r) for r in data["actions"]],
        recent=[ProviderCallOut.model_validate(c) for c in data["recent"]],
    )
