"""Profile refreshes and shop syncs do not come out of the seller's daily limit (v7 §D3)."""

from __future__ import annotations

import uuid

import httpx
from fakeredis import FakeAsyncRedis

from app.etsy.api import EtsyApiClient
from app.etsy.rate_limiter import DailyQuota
from app.workers import gate


def _quota(redis) -> DailyQuota:
    return DailyQuota(redis, global_daily_limit=10, pause_percent=90)


def _client(quota, *, upkeep: bool) -> EtsyApiClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
    return EtsyApiClient(client_id="k", shared_secret="s", http_client=http, quota=quota, upkeep=upkeep)


async def test_upkeep_counts_app_wide_but_not_against_the_seller() -> None:
    redis = FakeAsyncRedis()
    quota, tenant = _quota(redis), uuid.uuid4()
    await _client(quota, upkeep=True).get_listing(1, access_token="t", tenant_id=tenant, tenant_limit=5)
    await _client(quota, upkeep=True).get_listing(2, access_token="t", tenant_id=tenant, tenant_limit=5)
    await _client(quota, upkeep=False).get_listing(3, access_token="t", tenant_id=tenant, tenant_limit=5)
    tenant_used, global_used = await quota.usage(tenant)
    assert (tenant_used, global_used) == (1, 3)  # the seller's own request only; Etsy sees all three
    assert await quota.upkeep_usage(tenant) == 2


async def test_a_seller_at_their_limit_still_gets_upkeep_but_the_app_pause_holds() -> None:
    redis = FakeAsyncRedis()
    quota, tenant = _quota(redis), uuid.uuid4()

    class T:
        id, daily_quota = tenant, 0  # nothing left of their own

    from app.db.models import TenantStatus

    T.status = TenantStatus.active
    ctx = {"quota": quota}
    assert (await gate.check(ctx, T, "refresh_profile")).action == "run"
    assert (await gate.check(ctx, T, "run_publish_job")).action == "paused"
    for _ in range(9):  # the app reaches its 90% pause
        await quota.reserve_upkeep(uuid.uuid4())
    assert (await gate.check(ctx, T, "sync_shop_listings")).action == "paused"


# --- what an account's requests were spent on ----------------------------------------------------


async def test_requests_are_counted_by_what_they_were_for() -> None:
    import uuid as _uuid

    import httpx
    from fakeredis import FakeAsyncRedis

    from app.etsy.api import EtsyApiClient
    from app.etsy.calllog import current_job
    from app.etsy.rate_limiter import DailyQuota

    redis = FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=5000)
    tenant = _uuid.uuid4()
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

    def client(upkeep: bool) -> EtsyApiClient:
        return EtsyApiClient(client_id="k", shared_secret="s", http_client=http, quota=quota, upkeep=upkeep)

    async def calls(job: str, n: int, *, upkeep: bool) -> None:
        current_job.set(job)
        for i in range(n):
            await client(upkeep).get_listing(i, access_token="t", tenant_id=tenant, tenant_limit=1000)

    async with http:
        await calls("sync_sales:shop-1", 6, upkeep=True)
        await calls("backfill_ledger:shop-1", 4, upkeep=True)
        await calls("estimate_ledger:shop-1", 1, upkeep=True)
        await calls("run_publish_job:job-1", 3, upkeep=False)
        await calls("run_publish_live_job:job-2", 2, upkeep=False)
        await calls("something_new:x", 1, upkeep=False)

    spent = await quota.spending(tenant)
    assert spent == {
        "sales": {"own": 0, "upkeep": 6},
        "ledger": {"own": 0, "upkeep": 5},
        "drafts": {"own": 3, "upkeep": 0},
        "publishing": {"own": 2, "upkeep": 0},
        "other": {"own": 1, "upkeep": 0},
    }
    # It adds up to the counters the ceiling is checked against.
    assert sum(v["own"] for v in spent.values()) == (await quota.usage(tenant))[0] == 6
    assert sum(v["upkeep"] for v in spent.values()) == await quota.upkeep_usage(tenant) == 11
    assert await quota.spending(tenant, days_ago=1) == {}
