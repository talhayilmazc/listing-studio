"""Health endpoint: 200 only when the database and Redis answer (quality pass 2026-10, review R18)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
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
