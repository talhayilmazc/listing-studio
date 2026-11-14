"""The digest's p95 is real milliseconds, and an hour of slow answers is an alert (fix/perf-and-memory).

The production digest of 14 Nov 2026 said "60,000 ms" and "3,200 ms": its p95 was the top of a power-of-two
bucket (anything over 25.6 s, anything from 1.6 to 3.2 s). Now each request's own time is kept (the latest
2,000 per endpoint and day) and the p95 is one of them; counts from before say "up to" or "over". The time
is ours: the middleware leaves out the wait for the request's body (a seller's photo on a slow line).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.main import create_app
from app.monitor import rules, signals
from tests.auth_support import BROWSER_HEADERS


@pytest.fixture(autouse=True)
def _clean():
    signals.reset()
    yield
    signals.reset()


NOW = time.time()
DAY = time.strftime("%Y-%m-%d", time.gmtime(NOW))


async def _requests(redis, route_path: str, seconds: list[float], *, method: str = "GET", at: float = NOW) -> None:
    for s in seconds:
        signals.request_done(route_path, 200, s, now=at, method=method)
    await signals.flush(redis, "api", now=at)


async def test_the_p95_is_a_time_a_request_took_with_its_method() -> None:
    redis = FakeAsyncRedis()
    # 100 requests: 92 fast, 8 between 1.6 and 3.2 s (one bucket); the p95 is the 95th of them, as it was.
    times = [0.12] * 92 + [1.7, 1.9, 2.234, 2.4, 2.5, 2.7, 2.9, 3.1]
    await _requests(redis, "/api/batches/7d0b9a5e-0000-4000-8000-000000000001/content", times)
    await _requests(redis, "/api/batches/7d0b9a5e-0000-4000-8000-000000000001/assets", [0.2] * 10, method="POST")
    rows = await signals.slowest(redis, [DAY])
    assert rows[0] == ("GET /api/batches/{id}/content", 2234, 100, True)
    assert rows[1] == ("POST /api/batches/{id}/assets", 200, 10, True)
    assert signals.said(2234, True) == "2,234 ms" and signals.said(14_230, True) == "14.2 s"


async def test_counts_from_before_the_times_were_kept_say_up_to_or_over() -> None:
    redis = FakeAsyncRedis()
    await redis.hset(f"monitor:latency:{DAY}", mapping={"/api/batches/{id}/assets|11": 3, "/api/profiles|7": 4})
    rows = {r[0]: r for r in await signals.slowest(redis, [DAY])}
    assert rows["/api/batches/{id}/assets"] == ("/api/batches/{id}/assets", 60000, 3, False)
    assert signals.said(60000, False) == "over 25.6 s"
    assert signals.said(3200, False) == "up to 3,200 ms"


def _problems(found):
    return [p for p in found if p.key == "slow_endpoints"]


async def test_an_hour_of_slow_answers_is_one_alert() -> None:
    redis = FakeAsyncRedis()
    now = datetime.fromtimestamp(NOW, timezone.utc)
    # Four slow uploads in the hour: not a p95 yet.
    await _requests(redis, "/api/batches/x/generate", [12.0] * 4, method="POST", at=NOW - 600)
    assert await rules._slow(redis, now) == []
    # A fifth, and a fast endpoint beside it: one alert naming the slow one, in seconds.
    await _requests(redis, "/api/batches/x/generate", [12.5], method="POST", at=NOW - 60)
    await _requests(redis, "/api/profiles", [0.1] * 50, at=NOW - 60)
    found = _problems(await rules._slow(redis, now))
    assert len(found) == 1
    assert found[0].line == "p95 over the last hour above 10 s: POST /api/batches/x/generate 12.5 s (5 requests)."
    # An hour later those requests are out of the window.
    later = datetime.fromtimestamp(NOW + 3700, timezone.utc)
    assert await rules._slow(redis, later) == []


async def test_a_slow_line_sending_the_body_is_not_counted_as_our_time() -> None:
    import asyncio

    redis = FakeAsyncRedis()
    app = create_app()

    @app.post("/api/_upload")
    async def upload(request: Request) -> dict:
        await request.body()
        return {}

    sent = {"done": False}

    async def slow_body():  # the seller's line: 0.6 s for the body
        for _ in range(3):
            await asyncio.sleep(0.2)
            yield b"x" * 1000

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers=BROWSER_HEADERS) as client:
        started = time.perf_counter()
        res = await client.post("/api/_upload", content=slow_body())
        took = time.perf_counter() - started
        sent["done"] = res.status_code == 200
        assert res.status_code == 200, res.text
    await signals.flush(redis, "api")
    (route, p95, n, exact), = [r for r in await signals.slowest(redis, [time.strftime("%Y-%m-%d", time.gmtime())])
                               if r[0] == "POST /api/_upload"]
    assert sent["done"] and took >= 0.6 and n == 1 and exact
    # Our time is the whole answer less the 600 ms the body spent on the line (the rest is the app's own work,
    # a cold start's included).
    assert p95 <= took * 1000 - 550, f"our time {p95} ms of {took * 1000:.0f} includes the body's 600 ms on the line"
