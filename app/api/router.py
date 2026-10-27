from fastapi import APIRouter

from app.api.routes import health

# Every feature router is included here, under /api/v1.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
