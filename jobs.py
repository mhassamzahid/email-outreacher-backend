"""Background work that runs inside the API process: the follow-up check every 30 minutes.

It starts two minutes after the API does (so a short-lived process, like a test run, never reaches
it) and only does anything for workspaces with emails queued in a CheapSequencer campaign.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import select, text

from config import get_settings
from crud import (
    ExampleRepo, ExclusionRepo, IcpProfileRepo, ProductRepo, PromptRepo, ProviderCallRepo, SendingRepo, SettingsRepo,
)  # fmt: skip
from database import get_engine, get_sessionmaker
from lead_intel import LeadIntelRepo
from models import Email
from services import outreach
from services.generation import EmailWriter
from services.sending import SequencerClient

START_AFTER_SECONDS = 120
EVERY_SECONDS = 1800


async def workspaces_in_sequence() -> list[UUID]:
    async with get_sessionmaker()() as session:
        query = select(Email.workspace_id).where(Email.sequence_status.in_(("queued", "active"))).distinct()
        return list(await session.scalars(query))


@asynccontextmanager
async def deps_for(workspace_id: UUID) -> AsyncIterator[outreach.Deps]:
    settings = get_settings()
    sequencer = SequencerClient(settings.cheapsequencer_api_key)
    writer = EmailWriter(settings.anthropic_api_key) if settings.anthropic_api_key else None
    try:
        async with get_sessionmaker()() as session, get_engine().connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))  # Lead Intel is only ever read
            try:
                yield outreach.Deps(
                    workspace_id, SendingRepo(session), SettingsRepo(session), IcpProfileRepo(session), PromptRepo(session),
                    ProductRepo(session), ExampleRepo(session), ExclusionRepo(session), ProviderCallRepo(session),
                    LeadIntelRepo(conn), sequencer, writer,
                )  # fmt: skip
            finally:
                await conn.rollback()
    finally:
        await sequencer.aclose()
        if writer:
            await writer.aclose()


async def follow_ups_forever() -> None:
    await asyncio.sleep(START_AFTER_SECONDS)
    await outreach.follow_up_loop(deps_for, workspaces_in_sequence, EVERY_SECONDS)


def start(tasks: list[asyncio.Task]) -> None:
    """Start the background jobs this configuration can run."""
    if get_settings().cheapsequencer_api_key:
        tasks.append(asyncio.create_task(follow_ups_forever(), name="follow-ups"))
