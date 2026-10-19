"""Listyro's own limits under Etsy's Commercial Access (approved 19 October 2026).

Etsy now allows the app 150 requests a second and 100,000 a day, with no personal-access user
limit. Our own defaults stay well inside that, and every safety rule stays: one global limiter
without bursts, the 90% daily pause, a per-account ceiling, Retry-After on 429.
"""

from __future__ import annotations

import asyncio

from fakeredis import FakeAsyncRedis

from app.core.config import Settings
from app.etsy.rate_limiter import DailyQuota, TokenBucket


def test_the_defaults_are_the_commercial_access_ones() -> None:
    # The code's defaults (the server's .env may override them; deploy/env.production.example
    # carries the same values).
    d = {name: f.default for name, f in Settings.model_fields.items()}
    assert d["global_daily_limit"] == 100_000  # Etsy's own daily ceiling for the app
    assert d["global_pause_percent"] == 90  # new work waits at 90,000
    assert d["etsy_requests_per_second"] == 10  # far below Etsy's 150 a second
    assert d["account_daily_ceiling"] == 20_000  # one account cannot take the whole day
    assert (d["max_shops_per_tenant"], d["max_shops_app_wide"]) == (8, 200)
    # Never above Etsy's per-second limit, whatever the server's .env says.
    field = Settings.model_fields["etsy_requests_per_second"]
    assert any(getattr(m, "le", None) == 150 for m in field.metadata)


async def test_ten_a_second_is_reached_and_never_exceeded_without_a_burst() -> None:
    redis = FakeAsyncRedis()
    clock = {"t": 0.0}
    bucket = TokenBucket(redis, rate=10.0, time_func=lambda: clock["t"])  # capacity 1: no burst
    # Everyone asks at once: one request through, the next a tenth of a second away.
    first = await asyncio.gather(*(bucket.try_acquire() for _ in range(50)))
    assert sum(1 for ok, _ in first if ok) == 1
    assert all(abs(wait - 0.1) < 1e-3 for ok, wait in first if not ok)
    # Callers that wait their turn: exactly 10 in each one-second window, never 11.
    allowed: list[float] = []
    t = 0.0
    while t < 3.0:
        clock["t"] = t
        ok, wait = await bucket.try_acquire()
        if ok:
            allowed.append(t)
            t += 0.001
        else:
            t += max(wait, 0.001)
    for start in (0.0, 0.5, 1.0, 1.7):
        assert sum(1 for x in allowed if start <= x < start + 1.0) <= 10
    assert sum(1 for x in allowed if 1.0 <= x < 2.0) == 10  # and the rate is actually reached
    await redis.aclose()


async def test_the_daily_pause_is_ninety_percent_of_the_new_ceiling() -> None:
    redis = FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=100_000, pause_percent=90)
    assert quota.pause_at == 90_000
    await redis.aclose()


def test_the_production_example_sets_the_same_values() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "deploy" / "env.production.example"
    if not example.exists():
        import pytest

        pytest.skip("deploy/ is not here")
    lines = dict(line.split("=", 1) for line in example.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    assert (lines["GLOBAL_DAILY_LIMIT"], lines["GLOBAL_PAUSE_PERCENT"], lines["ETSY_REQUESTS_PER_SECOND"]) == ("100000", "90", "10")
    assert (lines["ACCOUNT_DAILY_CEILING"], lines["MAX_SHOPS_PER_TENANT"], lines["MAX_SHOPS_APP_WIDE"]) == ("20000", "8", "200")
