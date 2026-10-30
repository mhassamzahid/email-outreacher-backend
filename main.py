"""Entry point. Run with: uvicorn main:app --reload"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import jobs
from config import get_settings
from dependencies import get_current_user
from routers import (
    companies, emails, examples, exclusions, health, icps, improve, leads, mailboxes, pipeline, posts, products, prompts,
    sending, settings, stakeholders, usage, users, workspaces,
)  # fmt: skip


# Every feature router is included here, under /api/v1. Only health is open: the rest need a signed-in
# Supabase session (see dependencies.get_current_user).
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
protected = APIRouter(dependencies=[Depends(get_current_user)])
protected.include_router(users.router)
protected.include_router(icps.router)
protected.include_router(companies.router)
protected.include_router(leads.router)
protected.include_router(workspaces.router)
protected.include_router(stakeholders.router)
protected.include_router(posts.router)
protected.include_router(pipeline.router)
protected.include_router(usage.router)
protected.include_router(prompts.router)
protected.include_router(emails.router)
protected.include_router(improve.router)
protected.include_router(settings.router)
protected.include_router(products.router)
protected.include_router(examples.router)
protected.include_router(exclusions.router)
protected.include_router(mailboxes.router)
protected.include_router(sending.router)
api_router.include_router(protected)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Background jobs (the follow-up check) run alongside the API and stop with it."""
    tasks: list[asyncio.Task] = []
    jobs.start(tasks)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    # Unversioned liveness check for Railway's healthcheck.
    @app.get("/health", include_in_schema=False)
    def root_health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
