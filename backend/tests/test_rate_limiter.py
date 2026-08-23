"""Token bucket and daily quota tests."""

import asyncio
import uuid

from fakeredis import FakeAsyncRedis

from app.etsy.rate_limiter import DailyQuota, TokenBucket


# Targets 4 req/s (Personal App per-second limit is 5; 4 leaves margin).
def _bucket_at(redis: FakeAsyncRedis, clock: dict[str, float]) -> TokenBucket:
    return TokenBucket(redis, rate=4.0, capacity=4.0, time_func=lambda: clock["t"])


async def test_bucket_allows_capacity_then_blocks(fake_redis: FakeAsyncRedis) -> None:
    clock = {"t": 0.0}
    bucket = _bucket_at(fake_redis, clock)

    # 20 requests hit the bucket at the same instant; only capacity (4) pass.
    results = await asyncio.gather(*(bucket.try_acquire() for _ in range(20)))
    allowed = sum(1 for ok, _ in results if ok)
    assert allowed == 4

    # The 5th+ requests report a positive wait (< 1s at 4/s).
    denied_waits = [wait for ok, wait in results if not ok]
    assert denied_waits and all(0 < w <= 1.0 for w in denied_waits)


async def test_bucket_refills_over_time(fake_redis: FakeAsyncRedis) -> None:
    clock = {"t": 0.0}
    bucket = _bucket_at(fake_redis, clock)

    await asyncio.gather(*(bucket.try_acquire() for _ in range(4)))  # drain
    ok, _ = await bucket.try_acquire()
    assert ok is False  # empty

    clock["t"] = 1.0  # one second later -> 4 tokens refilled
    results = await asyncio.gather(*(bucket.try_acquire() for _ in range(20)))
    assert sum(1 for ok, _ in results if ok) == 4


async def test_default_rate_is_four(fake_redis: FakeAsyncRedis) -> None:
    # Default bucket (no explicit rate) enforces the 4 req/s target.
    bucket = TokenBucket(fake_redis, time_func=lambda: 0.0)
    results = await asyncio.gather(*(bucket.try_acquire() for _ in range(20)))
    assert sum(1 for ok, _ in results if ok) == 4


async def test_never_exceeds_rate_across_windows(fake_redis: FakeAsyncRedis) -> None:
    """Simulate concurrent load across several 1s windows; never > 4 per window."""
    clock = {"t": 0.0}
    bucket = _bucket_at(fake_redis, clock)

    for second in range(5):
        clock["t"] = float(second)
        results = await asyncio.gather(*(bucket.try_acquire() for _ in range(50)))
        assert sum(1 for ok, _ in results if ok) <= 4


async def test_quota_reserve_under_and_over_tenant_limit(fake_redis: FakeAsyncRedis) -> None:
    quota = DailyQuota(fake_redis, global_daily_limit=1000)
    tenant = uuid.uuid4()

    assert await quota.reserve(tenant, tenant_limit=2) is True
    assert await quota.reserve(tenant, tenant_limit=2) is True
    # Third exceeds the tenant's limit of 2 -> deferred.
    assert await quota.reserve(tenant, tenant_limit=2) is False

    tenant_used, _ = await quota.usage(tenant)
    assert tenant_used == 2  # rejected reservation was rolled back


async def test_quota_global_limit_blocks_and_rolls_back(fake_redis: FakeAsyncRedis) -> None:
    quota = DailyQuota(fake_redis, global_daily_limit=1)
    t1, t2 = uuid.uuid4(), uuid.uuid4()

    assert await quota.reserve(t1, tenant_limit=100) is True
    # Global budget (1) is spent; a different tenant is still blocked.
    assert await quota.reserve(t2, tenant_limit=100) is False

    t2_used, global_used = await quota.usage(t2)
    assert t2_used == 0  # tenant counter rolled back
    assert global_used == 1
