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


def keep_loaded(session) -> None:  # noqa: ANN001
    """Hold every object this session loads until the session ends.

    SQLAlchemy's identity map keeps loaded objects by weak reference: a helper that loads a
    listing's profile, uses it and drops it lets it be collected, and the next row's
    ``session.get`` queries the same profile again (a page of 150 listings ran 300 such
    lookups; the load test, scripts/load_test.py). An API request's session is short, so
    holding what it loaded costs little and every repeat ``get`` is answered from memory."""
    from sqlalchemy import event

    kept: list = session.sync_session.info.setdefault("kept", [])

    @event.listens_for(session.sync_session, "loaded_as_persistent")
    def _keep(_session, instance) -> None:  # noqa: ANN001
        kept.append(instance)
