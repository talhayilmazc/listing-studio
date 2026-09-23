"""Async database engine and session factory."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (asyncpg in production)."""
    # hide_parameters keeps bound values out of exception text: an IntegrityError
    # on registration would otherwise carry the email and password hash into
    # whatever logs the traceback.
    return create_async_engine(
        get_settings().database_url, pool_pre_ping=True, hide_parameters=True
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker:
    """Return a cached async session factory."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)
