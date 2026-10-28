"""Entry point. Run with: uvicorn main:app --reload"""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import companies, health, icps, leads, pipeline, posts, stakeholders, usage, workspaces


# Every feature router is included here, under /api/v1.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(icps.router)
api_router.include_router(companies.router)
api_router.include_router(leads.router)
api_router.include_router(workspaces.router)
api_router.include_router(stakeholders.router)
api_router.include_router(posts.router)
api_router.include_router(pipeline.router)
api_router.include_router(usage.router)


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
