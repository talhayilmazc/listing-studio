"""Every call to the AI provider is one row: who, what for, model, tokens, ok, cost."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core import ai_meter, ai_prices, alerts
from app.core.llm_status import LLMUnavailable
from app.db.models import AiCall, GeneratedContent
from app.pipeline.content import AnthropicContentGenerator, ContentValidationError
from app.pipeline.imageclass import AnthropicImageKindClassifier
from app.pipeline.llm import AnthropicLLMClient, LLMError
from app.pipeline.vision import AnthropicVisionAnalyzer
from tests.support import VALID_TITLE, FakeMessages, fake_response
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_regenerate import _generate, _group, llm  # noqa: F401  (fixtures)

MODEL = "claude-sonnet-5"
VISION = {"themes": ["mountain"], "embedded_text": "", "style": "flat", "colors": ["orange"],
          "target_audience": "hikers", "product_type_hints": ["poster"]}
GOOD = {"title": VALID_TITLE, "tags": [f"tag {i}" for i in range(13)], "description": "A description."}
BAD = {**GOOD, "tags": ["only one"]}
TABLE = ai_prices.merged(None)


def client(responses: list, model: str = MODEL) -> AnthropicLLMClient:
    return AnthropicLLMClient(api_key="k", model=model, messages_client=FakeMessages(responses))


async def calls(sm) -> list[AiCall]:
    async with sm() as s:
        return list((await s.execute(select(AiCall).order_by(AiCall.at))).scalars())


# --- prices -------------------------------------------------------------------------------------
def test_each_token_class_is_priced_at_its_own_rate() -> None:
    # Sonnet 5: $2 input, $10 output, $2.50 5-minute cache write, $4 1-hour write, $0.20 cache read.
    cost = ai_prices.cost(MODEL, TABLE, input_tokens=1_000_000, output_tokens=100_000,
                          cache_write_tokens=200_000, cache_write_1h_tokens=100_000, cache_read_tokens=500_000)
    assert cost == Decimal("2") + Decimal("1") + Decimal("0.5") + Decimal("0.4") + Decimal("0.1")
    assert ai_prices.cost("claude-haiku-4-5", TABLE, input_tokens=300, output_tokens=120) == Decimal("0.0009")


def test_a_dated_snapshot_takes_its_familys_price_and_an_unknown_model_has_none() -> None:
    assert ai_prices.price_for("claude-haiku-4-5-20251001", TABLE) == TABLE["claude-haiku-4-5"]
    # The longest matching name wins: Sonnet 5.5 is not priced as Sonnet 5 by accident.
    assert ai_prices.price_for("claude-sonnet-5-5", TABLE) is TABLE["claude-sonnet-5-5"]
    assert ai_prices.cost("some-new-model", TABLE, input_tokens=10, output_tokens=10) is None


def test_a_stored_price_overrides_the_default_and_bad_ones_are_refused() -> None:
    table = ai_prices.merged({MODEL: ai_prices.validate(
        {"input": "3", "output": "15", "cache_write": "3.75", "cache_write_1h": "6", "cache_read": "0,30"})})
    assert ai_prices.cost(MODEL, table, input_tokens=1_000_000, output_tokens=0) == Decimal("3")
    assert table[MODEL].cache_read == Decimal("0.30")
    for bad in ({"input": "abc"}, {"input": "-1", "output": "1", "cache_write": "1", "cache_write_1h": "1", "cache_read": "1"}):
        with pytest.raises(ValueError):
            ai_prices.validate(bad)


# --- the meter, at the client ----------------------------------------------------------------------
async def test_every_kind_of_call_is_a_row(async_sm) -> None:
    refusal = fake_response({"x": 1})
    refusal.stop_reason = "refusal"
    cached = fake_response(VISION, input_tokens=40, output_tokens=60, cache_read=900, cache_write=300)
    cached.usage.cache_creation = SimpleNamespace(ephemeral_5m_input_tokens=200, ephemeral_1h_input_tokens=100)
    cached.model = "claude-sonnet-5-20260801"

    class Down(Exception):
        status_code = 529

    class Limit(Exception):
        status_code = 400

        def __str__(self) -> str:
            return "You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC."

    async with async_sm() as s:
        async with ai_meter.scope(None, s):
            with ai_meter.purpose("vision"):
                await client([cached]).complete_json(system="s", content_blocks=[], schema={})
            with pytest.raises(LLMError):
                await client([refusal]).complete_json(system="s", content_blocks=[], schema={})
            for exc, raised in ((Down("overloaded"), Down), (Limit(), LLMUnavailable)):
                failing = client([])

                async def boom(exc=exc, **_):
                    raise exc

                failing._messages.create = boom
                with pytest.raises(raised):
                    await failing.complete_json(system="s", content_blocks=[], schema={})

    ok, refused, down, limit = await calls(async_sm)
    # The model that answered, uncached input, and both cache lifetimes apart.
    assert (ok.purpose, ok.model, ok.ok) == ("vision", "claude-sonnet-5-20260801", True)
    assert (ok.input_tokens, ok.output_tokens, ok.cache_write_tokens, ok.cache_write_1h_tokens, ok.cache_read_tokens) == (40, 60, 200, 100, 900)
    assert ok.cost_usd == ai_prices.cost(MODEL, TABLE, input_tokens=40, output_tokens=60, cache_write_tokens=200,
                                         cache_write_1h_tokens=100, cache_read_tokens=900)
    assert ok.day == ok.at.astimezone(timezone.utc).date() if ok.at.tzinfo else True
    # A refusal used tokens and is billed; it is a failed call with its cost.
    assert (refused.ok, refused.error, refused.input_tokens, refused.purpose) == (False, "refusal", 100, "other")
    assert refused.cost_usd and refused.cost_usd > 0
    # A request that never reached the model: a row, no tokens, no cost.
    assert (down.ok, down.error, down.input_tokens, down.cost_usd) == (False, "http_529", 0, Decimal("0"))
    assert (limit.ok, limit.error) == (False, "usage_limit")


async def test_purposes_come_from_the_component_and_a_retry_is_its_own(async_sm) -> None:
    async with async_sm() as s:
        async with ai_meter.scope(None, s) as meter:
            vision = await AnthropicVisionAnalyzer(client([fake_response(VISION)])).analyze(b"x", "image/png")
            generator = AnthropicContentGenerator(client([fake_response(BAD), fake_response(GOOD)]))
            await generator.generate(vision.analysis, "SKU1")
            meter.listing_written()
            assert await AnthropicImageKindClassifier(client([fake_response({"kind": "size_chart"})])).classify(b"x") == "size_chart"
    rows = await calls(async_sm)
    assert [r.purpose for r in rows] == ["vision", "content", "content_retry", "size_chart"]
    # The listing is counted once, on the call that wrote it.
    assert [r.listings for r in rows] == [0, 0, 1, 0]


async def test_calls_are_written_even_when_the_work_fails_and_rolls_back(async_sm) -> None:
    async with async_sm() as s:
        with pytest.raises(ContentValidationError):
            async with ai_meter.scope(None, s):
                await AnthropicContentGenerator(client([fake_response(BAD), fake_response(BAD)])).generate(
                    (await AnthropicVisionAnalyzer(client([fake_response(VISION)])).analyze(b"x", "image/png")).analysis
                )
    rows = await calls(async_sm)
    assert [(r.purpose, r.ok, r.listings) for r in rows] == [("vision", True, 0), ("content", True, 0), ("content_retry", True, 0)]
    assert all(r.cost_usd and r.cost_usd > 0 for r in rows)


async def test_outside_a_scope_nothing_breaks(async_sm) -> None:
    # Tests have no database behind a bare client (conftest turns unscoped writes off);
    # in production such a call is written through a session of its own.
    assert ai_meter.UNSCOPED_WRITES is False
    result = await client([fake_response({"a": 1})]).complete_json(system="s", content_blocks=[], schema={})
    assert result.data == {"a": 1} and await calls(async_sm) == []


# --- through the app ------------------------------------------------------------------------------
async def test_generating_a_listing_meters_every_call_for_that_seller(ctx, llm) -> None:  # noqa: F811
    batch, _, profile = await _group(ctx)
    title = ("Sunrise Mountain Printable Wall Art Digital Download Poster " * 3)[:130]
    good = {"title": title, "tags": [f"new{i}" for i in range(13)], "description": "New."}
    llm._responses = [fake_response(VISION, input_tokens=1500, output_tokens=200), fake_response({**good, "tags": ["one"]}), fake_response(good)]
    out = await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert out["generated"] == 1
    rows = await calls(ctx["sm"])
    assert [(r.purpose, r.ok, r.listings) for r in rows] == [("vision", True, 0), ("content", True, 0), ("content_retry", True, 1)]
    assert {r.tenant_id for r in rows} == {ctx["tenant_id"]}
    assert rows[0].input_tokens == 1500

    # A failed generation is metered too, with no listing.
    llm._responses = [fake_response(VISION), fake_response({**good, "tags": ["one"]}), fake_response({**good, "tags": ["one"]})]
    out = await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert out["failed"] == 1
    rows = await calls(ctx["sm"])
    assert len(rows) == 6 and sum(r.listings for r in rows) == 1


async def test_an_account_refusal_is_metered_and_still_pauses(ctx, llm, monkeypatch) -> None:  # noqa: F811
    from fakeredis import FakeAsyncRedis

    from app.api import deps

    async def quiet(text: str) -> bool:
        return True

    monkeypatch.setattr(alerts, "send", quiet)
    ctx["app"].dependency_overrides[deps.get_redis] = lambda: FakeAsyncRedis()
    batch, _, profile = await _group(ctx)

    class Limit(Exception):
        status_code = 400

        def __str__(self) -> str:
            return "Your credit balance is too low to access the Anthropic API."

    async def refuse(**_):
        raise Limit()

    llm.create = refuse
    out = await _generate(ctx, batch, profile, replace=True, confirm_approved=True)
    assert out["paused"] and out["failed"] == 0
    (row,) = await calls(ctx["sm"])
    assert (row.purpose, row.ok, row.error, row.cost_usd, row.tenant_id) == ("vision", False, "credit", Decimal("0"), ctx["tenant_id"])
    # The old listing is untouched by the pause.
    async with ctx["sm"]() as s:
        assert len(list((await s.execute(select(GeneratedContent))).scalars())) == 1


# --- the admin view ---------------------------------------------------------------------------------
def _call(tenant, purpose, *, day, model=MODEL, tokens=(1000, 100), ok=True, listings=0, cost="auto") -> AiCall:
    at = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    row = AiCall(at=at, day=day, tenant_id=tenant, purpose=purpose, model=model, ok=ok, error=None if ok else "refusal",
                 input_tokens=tokens[0], output_tokens=tokens[1], cache_write_tokens=0, cache_write_1h_tokens=0,
                 cache_read_tokens=0, listings=listings)
    row.cost_usd = ai_prices.cost_of(row, TABLE) if cost == "auto" else cost
    return row


async def test_admin_sees_today_and_this_month_per_seller_and_purpose(world) -> None:  # noqa: F811
    today = datetime.now(timezone.utc).date()
    earlier = today.replace(day=1) if today.day > 1 else today
    bob, admin = world["bob"].tenant_id, world["admin"].tenant_id
    async with world["sm"]() as s:
        s.add_all([
            _call(bob, "vision", day=today, tokens=(2000, 200)),
            _call(bob, "content", day=today, tokens=(3000, 500), ok=False),
            _call(bob, "content_retry", day=today, tokens=(3200, 500), listings=1),
            _call(bob, "size_chart", day=today, model="claude-haiku-4-5", tokens=(800, 10)),
            _call(admin, "vision", day=earlier, tokens=(1000, 100)),
            _call(None, "eval", day=today, tokens=(500, 50)),
            _call(bob, "content", day=today, model="brand-new-model", tokens=(100, 10), cost=None),
            _call(bob, "content", day=today - timedelta(days=45), tokens=(9_000_000, 0)),  # last month or older: not in "this month"
        ])
        await s.commit()

    assert (await world["b"].get("/api/admin/ai-cost")).status_code == 404  # sellers never
    seen = (await world["a"].get("/api/admin/ai-cost")).json()

    seller = next(x for x in seen["sellers"] if x["email"] == "bob@example.com")
    t = seller["today"]
    assert (t["calls"], t["failed"], t["listings"]) == (5, 1, 1)
    expected = (Decimal(2000 + 3000 + 3200) * 2 + Decimal(200 + 500 + 500) * 10 + Decimal(800) * 1 + Decimal(10) * 5) / 1_000_000
    assert Decimal(t["cost_usd"]) == expected and Decimal(t["cost_per_listing_usd"]) == expected
    assert t["unpriced"] is True and seen["unpriced_models"] == ["brand-new-model"]

    by_purpose = {p["purpose"]: p for p in seen["purposes"]}
    assert set(by_purpose) >= {"vision", "content", "content_retry", "size_chart", "eval"}
    assert by_purpose["content_retry"]["today"]["listings"] == 1 and by_purpose["content"]["today"]["failed"] == 1
    assert by_purpose["size_chart"]["label"] == "Size-chart check"
    ours = next(x for x in seen["sellers"] if x["id"] is None)
    assert ours["today"]["calls"] == 1  # the evaluation run: no seller

    # Today's total is the sum of the parts, and of the per-model lines the console can be checked against.
    assert Decimal(seen["today"]["cost_usd"]) == sum(Decimal(x["today"]["cost_usd"]) for x in seen["sellers"])
    day = next(d for d in seen["days"] if d["day"] == today.isoformat())
    assert Decimal(day["cost_usd"]) == sum(Decimal(m["cost_usd"]) for m in day["models"]) == Decimal(seen["today"]["cost_usd"])
    sonnet = next(m for m in day["models"] if m["model"] == MODEL)
    assert (sonnet["input_tokens"], sonnet["output_tokens"]) == (2000 + 3000 + 3200 + 500, 200 + 500 + 500 + 50)
    assert seen["recent"][0]["purpose"] and len(seen["recent"]) == 8
    assert "title" not in str(seen) and str(world["bob"].batch_id) not in str(seen)


async def test_setting_a_price_costs_the_calls_that_had_none_and_is_audited(world) -> None:  # noqa: F811
    today = datetime.now(timezone.utc).date()
    async with world["sm"]() as s:
        s.add(_call(world["bob"].tenant_id, "content", day=today, model="brand-new-model", tokens=(1_000_000, 100_000), cost=None))
        s.add(_call(world["bob"].tenant_id, "content", day=today, tokens=(1_000_000, 0)))
        await s.commit()
    body = {"model": "brand-new-model", "input": "4", "output": "20", "cache_write": "5", "cache_write_1h": "8", "cache_read": "0.4"}
    assert (await world["b"].put("/api/admin/ai-prices", json=body)).status_code == 404
    assert (await world["a"].put("/api/admin/ai-prices", json={**body, "input": "x"})).status_code == 422
    prices = (await world["a"].put("/api/admin/ai-prices", json=body)).json()
    assert next(p for p in prices if p["model"] == "brand-new-model")["custom"] is True

    seen = (await world["a"].get("/api/admin/ai-cost")).json()
    assert seen["unpriced_models"] == [] and seen["today"]["unpriced"] is False
    assert Decimal(seen["today"]["cost_usd"]) == Decimal("6") + Decimal("2")  # the new model now costed; the other unchanged

    # A changed price applies to calls from now on, not to ones already costed.
    await world["a"].put("/api/admin/ai-prices", json={**body, "model": MODEL})
    assert Decimal((await world["a"].get("/api/admin/ai-cost")).json()["today"]["cost_usd"]) == Decimal("8")
    back = (await world["a"].delete(f"/api/admin/ai-prices/{MODEL}")).json()
    assert next(p for p in back if p["model"] == MODEL) == {"model": MODEL, "input": "2.00", "output": "10.00",
                                                           "cache_write": "2.5000", "cache_write_1h": "4.00", "cache_read": "0.20", "custom": False}
