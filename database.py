"""Database connections: the engine, a read-only connection for Lead Intel, and sessions for our tables."""

from collections.abc import AsyncIterator
from functools import lru_cache
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


def to_async_url(raw: str) -> tuple[str, dict]:
    """Turn the Supabase connection string into an asyncpg URL plus connect args.

    Supabase hands out `postgres://` or `postgresql://` URLs, sometimes with `sslmode=require`;
    asyncpg takes neither the scheme nor that parameter, so both are translated here.
    """
    url = make_url(raw)
    connect_args: dict = {}
    sslmode = url.query.get("sslmode")
    if sslmode:
        url = url.difference_update_query(["sslmode"])
        if sslmode != "disable":
            connect_args["ssl"] = "require"
    url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False), connect_args


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    url, connect_args = to_async_url(settings.database_url)
    # Supabase's pooler (transaction mode) cannot keep prepared statements between transactions.
    # Both caches have to be off: asyncpg's own, and SQLAlchemy's (prepared_statement_cache_size);
    # with either one on, a statement prepared on one server connection gets reused on another
    # and fails with "prepared statement ... does not exist". Unique names avoid clashes.
    url = make_url(url).update_query_dict({"prepared_statement_cache_size": "0"}).render_as_string(hide_password=False)
    connect_args |= {
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    }
    # A request can hold two connections at once (read-only for Lead Intel, read-write for ours),
    # so the pool is sized for that. Supabase's pooler multiplexes these onto its own connections.
    return create_async_engine(url, connect_args=connect_args, pool_pre_ping=True, pool_size=10, max_overflow=20)


@lru_cache
def get_sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_readonly_conn() -> AsyncIterator[AsyncConnection]:
    """A connection whose transaction Postgres itself refuses to write in.

    Every read of Lead Intel's tables goes through here, so even a bug cannot change them.
    """
    try:
        engine = get_engine()
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    async with engine.connect() as conn:
        # BEGIN READ ONLY in one statement: a separate SET TRANSACTION costs a round trip on every request.
        await conn.execution_options(postgresql_readonly=True)
        try:
            yield conn
        finally:
            await conn.rollback()


async def get_lead_intel_icp_conn() -> AsyncIterator[AsyncConnection]:
    """A read-write connection for the single agreed Lead Intel write: adding an ICP.

    The writer commits its own insert; anything uncommitted is rolled back when the connection closes.
    """
    try:
        engine = get_engine()
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    async with engine.connect() as conn:
        yield conn


async def get_session() -> AsyncIterator[AsyncSession]:
    """A read-write session, for this service's own tables in the `outreach` schema."""
    try:
        sessionmaker = get_sessionmaker()
    except DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    async with sessionmaker() as session:
        yield session
