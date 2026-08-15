"""Async database engine and session factory."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (asyncpg in production)."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker:
    """Return a cached async session factory."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)
