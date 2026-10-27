from fastapi import APIRouter

from app.api.routes import companies, health, icps

# Every feature router is included here, under /api/v1.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(icps.router)
api_router.include_router(companies.router)
