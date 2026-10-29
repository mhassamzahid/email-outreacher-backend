"""Entry point. Run with: uvicorn main:app --reload"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from dependencies import get_current_user
from routers import (
    companies, emails, examples, exclusions, health, icps, improve, leads, pipeline, posts, products, prompts, settings,
    stakeholders, usage, users, workspaces,
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
api_router.include_router(protected)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

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
