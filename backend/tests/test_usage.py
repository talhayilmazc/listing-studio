"""Batched api_usage persistence tests."""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import ApiUsage, Tenant
from app.etsy.usage import UsageRecorder


async def _make_tenant(sm: async_sessionmaker) -> uuid.UUID:
    async with sm() as session:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        session.add(tenant)
        await session.commit()
        return tenant.id


async def test_flush_inserts_then_accumulates(async_sm: async_sessionmaker) -> None:
    tenant_id = await _make_tenant(async_sm)
    day = date(2026, 8, 15)
    recorder = UsageRecorder(flush_threshold=1000)

    recorder.record(tenant_id, day, 3)
    async with async_sm() as session:
        await recorder.flush(session)
    async with async_sm() as session:
        row = await session.get(ApiUsage, (tenant_id, day))
        assert row is not None and row.request_count == 3

    # A second flush accumulates onto the existing row (upsert semantics).
    recorder.record(tenant_id, day, 2)
    async with async_sm() as session:
        await recorder.flush(session)
    async with async_sm() as session:
        row = await session.get(ApiUsage, (tenant_id, day))
        assert row is not None and row.request_count == 5


async def test_maybe_flush_respects_threshold(async_sm: async_sessionmaker) -> None:
    tenant_id = await _make_tenant(async_sm)
    day = date(2026, 8, 15)
    recorder = UsageRecorder(flush_threshold=2)

    recorder.record(tenant_id, day, 1)
    async with async_sm() as session:
        await recorder.maybe_flush(session)  # below threshold -> no write
    assert recorder.pending == 1
    async with async_sm() as session:
        assert await session.get(ApiUsage, (tenant_id, day)) is None

    recorder.record(tenant_id, day, 1)  # now at threshold
    async with async_sm() as session:
        await recorder.maybe_flush(session)
    assert recorder.pending == 0
    async with async_sm() as session:
        row = await session.get(ApiUsage, (tenant_id, day))
        assert row is not None and row.request_count == 2
