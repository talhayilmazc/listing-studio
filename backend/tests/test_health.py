"""Health endpoint: 200 only when the database and Redis answer (quality pass 2026-10, review R18)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.db.session import _say_connection_lost
from app.main import create_app


async def _probe(*, database_ok: bool = True, redis_ok: bool = True) -> tuple[int, dict]:
    app = create_app()
    engine = create_async_engine("sqlite+aiosqlite://")
    sm = async_sessionmaker(engine)

    async def _session() -> AsyncIterator:
        async with sm() as s:
            if not database_ok:
                await engine.dispose()

                async def broken(*_a, **_k):  # noqa: ANN002, ANN003
                    raise OSError("database unreachable")

                s.execute = broken  # type: ignore[method-assign]
            yield s

    redis = FakeAsyncRedis()
    if not redis_ok:
        async def down() -> None:
            raise ConnectionError("redis unreachable")

        redis.ping = down  # type: ignore[method-assign]
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/health")
    await engine.dispose()
    return res.status_code, res.json()


async def test_health_is_ok_when_both_answer() -> None:
    assert await _probe() == (200, {"status": "ok"})


async def test_health_says_which_part_is_down() -> None:
    assert await _probe(database_ok=False) == (503, {"status": "unhealthy", "failed": ["database"]})
    assert await _probe(redis_ok=False) == (503, {"status": "unhealthy", "failed": ["redis"]})


async def test_a_busy_pool_is_not_a_database_that_is_down(tmp_path) -> None:  # noqa: ANN001
    """Every pooled connection taken: the check still reaches the database, without waiting for one."""
    app = create_app()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/h.db", pool_size=1, max_overflow=0, pool_timeout=30)
    sm = async_sessionmaker(engine)

    async def _session() -> AsyncIterator:
        async with sm() as s:
            yield s

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: FakeAsyncRedis()
    async with engine.connect():  # the pool's only connection
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await asyncio.wait_for(c.get("/health"), 5)
    await engine.dispose()
    assert (res.status_code, res.json()) == (200, {"status": "ok"})


async def test_a_pool_that_stays_busy_says_try_again_not_internal_error() -> None:
    app = create_app()

    @app.get("/api/_busy")
    async def _busy() -> None:
        raise SQLAlchemyTimeoutError("QueuePool limit of size 10 overflow 5 reached")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/api/_busy")
    assert res.status_code == 503
    assert res.headers["retry-after"] == "5"
    assert "Try again" in res.json()["detail"]
    assert "QueuePool" not in res.text


@pytest.mark.parametrize(("error", "says"), [(asyncio.CancelledError(), False), (OSError("connection closed"), True)])
def test_only_a_really_lost_connection_is_reported_as_one(caplog, error: BaseException, says: bool) -> None:  # noqa: ANN001
    """/health's own time limit cancelling its statement was logged as the idle timeout firing."""
    context = SimpleNamespace(is_disconnect=True, original_exception=error, statement="SELECT 1")
    with caplog.at_level(logging.ERROR, logger="app.db.session"):
        _say_connection_lost(context)
    assert ("idle_in_transaction_session_timeout" in caplog.text) is says
