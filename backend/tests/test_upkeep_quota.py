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
