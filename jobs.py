"""The follow-up check, as plain async code that a Celery task runs (see tasks.py and celery_app.py).

Each run looks at every workspace with emails queued in a CheapSequencer campaign: it records sends,
replies and stops, and writes the follow-ups that are nearly due. Nothing is kept between runs: each
one works out what's due from CheapSequencer and the database, so a late or skipped run loses nothing.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from config import get_settings
from crud import (
    ExampleRepo, ExclusionRepo, IcpCampaignRepo, IcpProfileRepo, ProductRepo, PromptRepo, ProviderCallRepo, SendingRepo, SettingsRepo,
)  # fmt: skip
from database import get_engine, get_sessionmaker
from lead_intel import LeadIntelRepo
from models import Email
from services import outreach
from services.generation import EmailWriter
from services.sending import SequencerClient

log = logging.getLogger("coldbrew.jobs")


async def workspaces_in_sequence() -> list[UUID]:
    async with get_sessionmaker()() as session:
        query = select(Email.workspace_id).where(Email.sequence_status.in_(("queued", "active"))).distinct()
        return list(await session.scalars(query))


@asynccontextmanager
async def deps_for(workspace_id: UUID) -> AsyncIterator[outreach.Deps]:
    settings = get_settings()
    sequencer = SequencerClient(settings.cheapsequencer_api_key, base_url=settings.cheapsequencer_api_url)
    writer = EmailWriter(settings.anthropic_api_key, base_url=settings.anthropic_base_url) if settings.anthropic_api_key else None
    try:
        async with get_sessionmaker()() as session, get_engine().connect() as conn:
            await conn.execution_options(postgresql_readonly=True)  # Lead Intel is only ever read
            try:
                yield outreach.Deps(
                    workspace_id, SendingRepo(session), SettingsRepo(session), IcpProfileRepo(session), PromptRepo(session),
                    ProductRepo(session), ExampleRepo(session), ExclusionRepo(session), ProviderCallRepo(session),
                    LeadIntelRepo(conn), sequencer, writer, IcpCampaignRepo(session),
                )  # fmt: skip
            finally:
                await conn.rollback()
    finally:
        await sequencer.aclose()
        if writer:
            await writer.aclose()


@dataclass
class CheckSummary:
    workspaces: int = 0
    checked: int = 0
    written: int = 0
    ended: int = 0
    cost_usd: float = 0.0
    failed: list[str] = field(default_factory=list)


async def check_follow_ups() -> CheckSummary:
    """One follow-up check across every workspace. One failing workspace never stops the others.
    Called on a fresh event loop each time, so the database pool is closed again before returning."""
    summary = CheckSummary()
    try:
        if not get_settings().cheapsequencer_api_key:
            return summary
        for workspace_id in await workspaces_in_sequence():
            summary.workspaces += 1
            try:
                async with deps_for(workspace_id) as deps:
                    run = await outreach.run_follow_ups(deps)
                summary.checked += run.checked
                summary.written += run.written
                summary.ended += run.ended
                summary.cost_usd += run.cost_usd
                if run.notes:
                    log.info("follow-ups for %s: %s", workspace_id, run.notes)
            except Exception:  # noqa: BLE001
                log.exception("follow-ups failed for workspace %s", workspace_id)
                summary.failed.append(str(workspace_id))
    finally:
        # Pooled connections belong to this run's event loop; the next run gets a new loop.
        await get_engine().dispose()
    return summary
