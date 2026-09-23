"""Etsy request pacing under concurrent jobs (docs/duzeltmeler-v5.md §A).

Publishing hit 429 "Exceeded per second rate limit" with four publish jobs
running at once. Nothing bypassed the bucket. The bucket itself was the leak: it
held 4 tokens and refilled 4 per second, so after an idle moment it let the
stored burst *and* the refill through, up to 7 requests in one second. These
tests run whole jobs on a virtual clock and count requests in every sliding
one-second window, which is what the old fixed-window tests never did.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import uuid

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.etsy.api import RATE_LIMIT_RETRIES, EtsyApiClient
from app.etsy.calllog import CallLog, current_job
from app.etsy.errors import EtsyRateLimited
from app.etsy.rate_limiter import DailyQuota, TokenBucket


class VirtualTime:
    """A shared clock that only moves when every task is waiting on it."""

    def __init__(self) -> None:
        self.t = 0.0
        self._waiters: list = []
        self._seq = itertools.count()

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (self.t + max(0.0, seconds), next(self._seq), fut))
        await fut

    async def run(self, *coros):
        tasks = [asyncio.create_task(c) for c in coros]
        while True:
            for _ in range(200):  # real yields: let every task run until it waits
                await asyncio.sleep(0)
            if all(t.done() for t in tasks):
                return [t.result() for t in tasks]
            if not self._waiters:
                raise RuntimeError("tasks are stuck on something other than the clock")
            when, _, fut = heapq.heappop(self._waiters)
            self.t = max(self.t, when)
            fut.set_result(None)


def busiest_second(times: list[float]) -> int:
    """Most requests in any window [t, t + 1s), over every start point t."""
    times = sorted(times)
    return max(sum(1 for u in times if s <= u < s + 1.0) for s in times)


def _world(vt: VirtualTime, respond, *, rate: float = 3.0, capacity: float = 1.0):
    redis = FakeAsyncRedis()
    bucket = TokenBucket(redis, rate=rate, capacity=capacity, time_func=vt.now, sleep_func=vt.sleep)
    sent: list[tuple[float, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = respond(len(sent))
        sent.append((vt.now(), response.status_code))
        return response

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = EtsyApiClient(
        client_id="k",
        shared_secret="s",
        http_client=http,
        bucket=bucket,
        quota=DailyQuota(redis, global_daily_limit=5000),
        call_log=CallLog(redis, time_func=vt.now),
        sleep=vt.sleep,
    )
    return client, http, sent, redis


async def _job(vt: VirtualTime, client: EtsyApiClient, tenant, calls: int, start: float, work: float):
    await vt.sleep(start)
    for i in range(calls):
        await client.get_listing(i, access_token="t", tenant_id=tenant, tenant_limit=1000)
        await vt.sleep(work)  # the job's own work between requests


def _ok(_n: int) -> httpx.Response:
    return httpx.Response(200, json={})


@pytest.mark.parametrize("work", [0.0, 0.3])
async def test_fifty_requests_from_concurrent_jobs_never_exceed_three_in_any_second(work) -> None:
    vt = VirtualTime()
    client, http, sent, _ = _world(vt, _ok)
    tenant = uuid.uuid4()
    async with http:
        await vt.run(*(_job(vt, client, tenant, 10, j * 0.05, work) for j in range(5)))

    assert len(sent) == 50
    assert busiest_second([t for t, _ in sent]) <= 3


async def test_the_old_bucket_is_what_broke_the_limit() -> None:
    """Regression record: the previous settings, same five jobs, exceed Etsy's 5."""
    vt = VirtualTime()
    client, http, sent, _ = _world(vt, _ok, rate=4.0, capacity=4.0)
    tenant = uuid.uuid4()
    async with http:
        await vt.run(*(_job(vt, client, tenant, 10, j * 0.05, 0.0) for j in range(5)))

    assert busiest_second([t for t, _ in sent]) > 5


async def test_retries_after_a_429_also_go_through_the_bucket() -> None:
    """Every seventh request is refused. Retries still count toward the limit."""
    vt = VirtualTime()

    def respond(n: int) -> httpx.Response:
        if n % 7 == 6:
            return httpx.Response(429, headers={"retry-after": "2"}, json={})
        return httpx.Response(200, json={})

    client, http, sent, _ = _world(vt, respond)
    tenant = uuid.uuid4()
    async with http:
        await vt.run(*(_job(vt, client, tenant, 10, j * 0.05, 0.0) for j in range(5)))

    refused = [t for t, status in sent if status == 429]
    assert refused, "the scenario must include 429s"
    assert sum(1 for _, status in sent if status == 200) == 50  # every call got through
    assert busiest_second([t for t, _ in sent]) <= 3  # retries included
    # Retry-After is honoured by *every* worker: nothing is sent for 2s after a 429.
    for t in refused:
        assert not [u for u, _ in sent if t < u < t + 2.0]


async def test_a_persistent_429_gives_up_after_the_retries_each_counted_against_the_quota() -> None:
    vt = VirtualTime()
    client, http, sent, redis = _world(vt, lambda n: httpx.Response(429, json={}))
    tenant = uuid.uuid4()
    async with http:
        with pytest.raises(EtsyRateLimited):
            await vt.run(client.get_listing(1, access_token="t", tenant_id=tenant, tenant_limit=1000))

    assert len(sent) == RATE_LIMIT_RETRIES + 1
    # Without Retry-After: exponential backoff, 1, 2, 4, 8 seconds.
    gaps = [b - a for (a, _), (b, _) in zip(sent, sent[1:])]
    assert [round(g) for g in gaps] == [1, 2, 4, 8]
    used, _ = await DailyQuota(redis).usage(tenant)
    assert used == RATE_LIMIT_RETRIES + 1


async def test_a_long_retry_after_is_not_waited_out() -> None:
    vt = VirtualTime()
    client, http, sent, _ = _world(
        vt, lambda n: httpx.Response(429, headers={"retry-after": "3600"}, json={})
    )
    async with http:
        with pytest.raises(EtsyRateLimited):
            await vt.run(client.get_listing(1, access_token="t"))
    assert len(sent) == 1


async def test_every_call_is_logged_with_its_job_and_the_busy_second(caplog) -> None:
    vt = VirtualTime()
    client, http, _, _ = _world(
        vt, lambda n: httpx.Response(429 if n == 0 else 200, headers={"retry-after": "1"}, json={})
    )
    current_job.set("run_publish_job:abc")
    with caplog.at_level(logging.INFO, logger="app.etsy.calls"):
        async with http:
            await vt.run(client.get_listing(7, access_token="secret-token"))

    lines = [r.getMessage() for r in caplog.records if r.name == "app.etsy.calls"]
    assert len(lines) == 2
    refused, ok = lines
    assert "GET /application/listings/7" in refused and "status=429" in refused
    assert "job=run_publish_job:abc" in refused and "calls_this_second=1" in refused
    assert "calls_previous_second=0" in refused
    assert "status=200" in ok and "attempt=2" in ok
    assert all("secret-token" not in line for line in lines)


async def test_the_default_clock_is_redis_shared_by_every_worker() -> None:
    """No per-process clock: two buckets on the same Redis see one timeline."""
    redis = FakeAsyncRedis()
    one, two = TokenBucket(redis), TokenBucket(redis)
    assert (await one.try_acquire())[0] is True
    allowed, wait = await two.try_acquire()
    assert allowed is False and 0 < wait <= 1 / 3 + 1e-3
