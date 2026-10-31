"""Async database engine and session factory."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _say_connection_lost(context: Any) -> None:
    """Says so in the log when a connection is gone from under an open transaction. Postgres ends one
    left idle past ``db_idle_transaction_seconds`` (something held it across slow work:
    core/db_release.py) and closes the connection; asyncpg only sees a closed connection, at the next
    statement. Postgres's own log names it: "terminating connection due to idle-in-transaction timeout".

    A statement cancelled on our side also counts as a disconnect to SQLAlchemy, and is neither."""
    if context.is_disconnect and isinstance(context.original_exception, Exception):
        logger.error(
            "idle_in_transaction_session_timeout fired, or the database restarted: a connection was lost with a "
            "transaction open (limit %s s). Next statement: %s",
            get_settings().db_idle_transaction_seconds, " ".join(str(context.statement or "").split())[:200])


def _watch_idle_timeout(engine: AsyncEngine) -> None:
    event.listen(engine.sync_engine, "handle_error", _say_connection_lost)


@lru_cache
def _probe_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, poolclass=NullPool, hide_parameters=True)


async def database_answers(session: AsyncSession) -> None:
    """Asks the database itself, on a connection of its own (the health check). Waiting for a free
    connection from the pool says how busy the app is, not whether the database is up: in the load
    test /health said "database unreachable" while the pool was busy and the database answering."""
    if session.bind is None:
        await session.execute(text("SELECT 1"))
        return
    probe = _probe_engine(session.bind.engine.url.render_as_string(hide_password=False))
    await session.execute(text("SELECT 1"), bind_arguments={"bind": probe.sync_engine})


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine (asyncpg in production)."""
    # hide_parameters keeps bound values out of exception text: an IntegrityError
    # on registration would otherwise carry the email and password hash into
    # whatever logs the traceback.
    settings = get_settings()
    options: dict[str, Any] = {}
    if settings.database_url.startswith("postgresql"):
        # Connections are kept, not closed and reopened: with SQLAlchemy's default of 5 kept and 10
        # thrown away after each use, every request beyond five opened a new connection, and asyncpg
        # asks the server about its types on each (42,294 times in one load test run).
        options = {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_pool_overflow,
            "pool_recycle": 3600,
            "pool_timeout": settings.db_pool_timeout,
            "connect_args": {"server_settings": {
                "idle_in_transaction_session_timeout": str(settings.db_idle_transaction_seconds * 1000)}},
        }
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True, **options)
    _watch_idle_timeout(engine)
    return engine


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
