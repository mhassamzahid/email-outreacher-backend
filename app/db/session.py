from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.engine import DatabaseNotConfigured, get_engine, get_sessionmaker


async def get_readonly_conn() -> AsyncIterator[AsyncConnection]:
    """A connection whose transaction Postgres itself refuses to write in.

    Every read of Lead Intel's tables goes through here, so even a bug cannot change them.
    """
    try:
        engine = get_engine()
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield conn
        finally:
            await conn.rollback()


async def get_session() -> AsyncIterator[AsyncSession]:
    """A read-write session, for this service's own tables in the `outreach` schema."""
    try:
        sessionmaker = get_sessionmaker()
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    async with sessionmaker() as session:
        yield session
