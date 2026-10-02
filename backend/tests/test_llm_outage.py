"""Our AI provider refusing the account pauses writing; it never fails a design."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.api import deps
from app.core import alerts, llm_status
from app.db.models import Asset
from tests.support import fake_response
from tests.test_regenerate import _generate, _group, llm  # noqa: F401  (fixtures)
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

USAGE = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
    "'You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC.'}}"
)


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


def test_only_an_account_refusal_counts_as_an_outage() -> None:
    usage = llm_status.classify(400, USAGE)
    assert usage is not None and usage.kind == "usage_limit"
    assert usage.resumes_at == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert llm_status.classify(400, "Your credit balance is too low to access the Anthropic API.").kind == "credit"
    assert llm_status.classify(401, "invalid x-api-key").kind == "auth"
    # Busy, rate-limited or a bad request about the content: ordinary errors.
    assert llm_status.classify(529, "Overloaded") is None
    assert llm_status.classify(429, "rate_limit_error") is None
    assert llm_status.classify(400, "messages: text content blocks must be non-empty") is None


async def test_the_operator_is_alerted_once_and_a_success_clears_it(monkeypatch) -> None:
    sent: list[str] = []

    async def fake_send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", fake_send)
    redis = FakeAsyncRedis()
    outage = llm_status.classify(400, USAGE)
    for _ in range(3):
        await llm_status.report(redis, outage)
    assert len(sent) == 1 and "PAUSED" in sent[0] and "2026-11-01 00:00 UTC" in sent[0]
    assert (await llm_status.current(redis))["kind"] == "usage_limit"
    await llm_status.cleared(redis)
    assert await llm_status.current(redis) is None
    await llm_status.report(redis, outage)  # a new outage is a new alert
    assert len(sent) == 2


@pytest.fixture()
def quiet_alerts(monkeypatch):
    sent: list[str] = []

    async def fake_send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", fake_send)
    return sent


async def test_generation_pauses_and_records_nothing_against_the_design(ctx, llm, quiet_alerts) -> None:  # noqa: F811
    redis = FakeAsyncRedis()
    ctx["app"].dependency_overrides[deps.get_redis] = lambda: redis
    batch, _, profile = await _group(ctx)

    async def refuse(**kwargs):
        llm.calls.append(kwargs)
        raise Refused(400, USAGE)

    llm.create = refuse
    first = await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert first["paused"] and first["failed"] == 0 and first["failures"] == []
    async with ctx["sm"]() as s:
        assert all(a.error is None for a in (await s.execute(select(Asset))).scalars())
    assert len(quiet_alerts) == 1

    # While paused, the provider is not called at all, and the seller is told.
    calls = len(llm.calls)
    again = await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert again["paused"] and len(llm.calls) == calls
    quota = (await ctx["client"].get("/api/quota")).json()
    assert quota["generation_pause"] and "on our side" in quota["generation_pause"]

    # The account is fixed: the next try after the pause runs, and clears it.
    await redis.delete(llm_status.OUTAGE_KEY)
    llm.create = type(llm).create.__get__(llm)
    llm._responses = [
        fake_response({"themes": ["mountain"], "embedded_text": "", "style": "flat", "colors": ["orange"],
                       "target_audience": "hikers", "product_type_hints": ["poster"]}),
    ]
    await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert (await ctx["client"].get("/api/quota")).json()["generation_pause"] is None
