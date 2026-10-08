"""Requests per draft are planned at the measured average plus a margin, not the
worst case: the worst case would block real work (a 16-shop seller's ~240 drafts)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli import draft_cost
from app.core import request_cost as rc
from app.db.models import ApiUsage, EtsyConnection, Job, JobStatus, JobType, Tenant
from app.etsy.rate_limiter import PAUSE_TENANT, DailyQuota
from app.etsy.usage import UsageRecorder
from app.pipeline.targets import ESTIMATED_CALLS_PER_DRAFT
from app.workers import gate

TODAY = datetime.now(UTC).date()


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    rc.forget()
    yield
    rc.forget()


def test_the_estimate_is_the_average_plus_a_margin_never_above_the_worst_case() -> None:
    assert (rc.DEFAULT_PER_DRAFT, rc.WORST_CASE) == (ESTIMATED_CALLS_PER_DRAFT, gate.JOB_COST["run_publish_job"])
    few = rc.estimate_from(300, 19, None)
    assert (few.per_draft, few.source, few.measured_mean) == (15, "default", 300 / 19)
    e = rc.estimate_from(1600, 100, None)  # 16 a draft, + 25%
    assert (e.per_draft, e.source, e.measured_mean) == (20, "measured", 16.0)
    assert rc.estimate_from(10_000, 100, None).per_draft == 45  # a bad day never plans above the worst case


async def _measured(sm: async_sessionmaker, *, drafts: int, requests: int) -> uuid.UUID:
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x", etsy_ceiling_override=None)
        s.add(tenant)
        await s.flush()
        shop = EtsyConnection(tenant_id=tenant.id, etsy_user_id=1, shop_id=1)
        s.add(shop)
        await s.flush()
        yesterday = TODAY - timedelta(days=1)
        s.add(ApiUsage(tenant_id=tenant.id, usage_date=yesterday, request_count=requests + 50,
                       categories={"drafts": requests, "publishing": 50}))
        # Before the breakdown was kept: not used. Today: still running, not used.
        s.add(ApiUsage(tenant_id=tenant.id, usage_date=yesterday - timedelta(days=1), request_count=999))
        s.add(ApiUsage(tenant_id=tenant.id, usage_date=TODAY, request_count=99, categories={"drafts": 99}))
        at = datetime(yesterday.year, yesterday.month, yesterday.day, 12, tzinfo=UTC)
        for n in range(drafts):
            s.add(Job(tenant_id=tenant.id, connection_id=shop.id, type=JobType.create_draft, payload={},
                      status=JobStatus.succeeded if n % 10 else JobStatus.failed, finished_at=at))
        await s.commit()
        return tenant.id


async def test_the_average_is_measured_from_api_usage_and_finished_drafts(async_sm: async_sessionmaker) -> None:
    await _measured(async_sm, drafts=40, requests=36 * 17)  # 36 finished; the 4 failed ones' requests count too
    async with async_sm() as s:
        e = await rc.measure(s)
    assert (e.drafts, e.requests, e.measured_mean, e.per_draft, e.since) == (36, 612, 17.0, 22, TODAY - timedelta(days=1))
    report = await draft_cost(async_sm)
    assert "measured average: 17.0" in report and "estimate used: 22 per draft" in report and "worst case: 45" in report
    assert "240 drafts (a 16-shop day): 5,280 estimated, 10,800 at the worst case" in report


async def test_the_recorder_keeps_the_categories(async_sm: async_sessionmaker) -> None:
    tenant_id = await _measured(async_sm, drafts=0, requests=0)
    day = date(2026, 1, 5)
    recorder = UsageRecorder()
    for category in ("drafts", "drafts", "publishing", None):
        recorder.record(tenant_id, day, category=category)
    async with async_sm() as s:
        await recorder.flush(s)
        recorder.record(tenant_id, day, category="drafts")
        await recorder.flush(s)
        row = await s.get(ApiUsage, (tenant_id, day))
        assert row.request_count == 5 and row.categories == {"drafts": 3, "publishing": 1, "other": 1}


async def test_a_draft_is_admitted_on_the_estimate_not_the_worst_case(async_sm: async_sessionmaker) -> None:
    tenant_id = await _measured(async_sm, drafts=110, requests=99 * 16)  # 16 a draft -> 20 planned
    quota = DailyQuota(FakeAsyncRedis(), global_daily_limit=5000)
    async with async_sm() as s:
        tenant = await s.get(Tenant, tenant_id)
        tenant.etsy_ceiling_override = 4500
        await s.commit()
    for _ in range(4470):  # 30 left: room for a 20-request draft, not a 45-request one
        await quota.reserve(tenant_id, 4500)
    ctx = {"quota": quota, "sessionmaker": async_sm}
    assert await gate.job_cost(ctx, "run_publish_job") == 20
    assert (await gate.check(ctx, tenant, "run_publish_job")).action == "run"
    for _ in range(15):
        await quota.reserve(tenant_id, 4500)
    verdict = await gate.check(ctx, tenant, "run_publish_job")
    assert verdict.action == "paused" and verdict.reason == PAUSE_TENANT
