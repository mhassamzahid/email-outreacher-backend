from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from config import Settings, get_settings
from database import get_readonly_conn
from lead_intel import lead_intel_metadata


router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str


class DatabaseHealth(BaseModel):
    status: str
    # Lead Intel columns this service reads but the live database does not have. Empty is healthy.
    missing_columns: list[str]


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@router.get("/health/db", response_model=DatabaseHealth)
async def database_health(conn: AsyncConnection = Depends(get_readonly_conn)) -> DatabaseHealth:
    """Check the connection and that Lead Intel still has every column we read. Read-only."""
    rows = await conn.execute(
        text(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = any(:tables)"
        ),
        {"tables": [t.name for t in lead_intel_metadata.sorted_tables]},
    )
    live = {(table, column) for table, column in rows}
    missing = [
        f"{table.name}.{column.name}"
        for table in lead_intel_metadata.sorted_tables
        for column in table.columns
        if (table.name, column.name) not in live
    ]
    return DatabaseHealth(status="ok" if not missing else "schema_mismatch", missing_columns=missing)
