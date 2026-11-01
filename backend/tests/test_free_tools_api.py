"""The optional AI ideas tool: its flag, Turnstile, the limit per visitor, the daily cap, and the meter."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.api import free_tools
from app.core import alerts, ai_meter, turnstile
from app.core.config import get_settings, set_settings_override
from app.core.llm_status import LLMUnavailable
from app.db.models import AiCall
from app.pipeline import listing_ideas
from app.pipeline.llm import LLMError, LLMResult, Usage
from tests.test_admin import world  # noqa: F401  (fixture)

GOOD = {
    "titles": ["Ceramic Frog Mug, Hand Painted Green Glaze, Stoneware Cup", "Frog Coffee Mug, Green Pottery"],
    "tags": ["frog lover mug", "pottery cup", "hand thrown stoneware", "forest kitchen", "toad coffee cup"],
}
BODY = {"description": "A green ceramic mug with a small frog painted on it", "turnstile_token": "tok"}


class FakeModel:
    """Stands in for the provider and writes a metered row the way the real client does."""

    def __init__(self, data=None, raises=None, cost_tokens=1000):
        self.data, self.raises, self.cost_tokens, self.calls = data or GOOD, raises, cost_tokens, []

    async def complete_json(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.raises:
            await ai_meter.record(model="claude-haiku-5-5", usage=None, ok=False, error="failed")
            raise self.raises
        usage = Usage(input_tokens=self.cost_tokens, output_tokens=self.cost_tokens, model="claude-haiku-5-5")
        await ai_meter.record(model="claude-haiku-5-5", usage=usage, ok=True)
        return LLMResult(data=self.data, usage=usage)


@pytest.fixture
def on(monkeypatch):
    base = get_settings()
    set_settings_override(base.model_copy(update={
        "free_tool_ai_enabled": True, "turnstile_site_key": "site", "turnstile_secret_key": "secret",
        "free_tool_ai_daily_cap_usd": 1.0, "free_tool_ai_per_visitor": 3,
    }))

    async def yes(token, client=None):  # noqa: ANN001, ANN202
        return True

    monkeypatch.setattr(turnstile, "verified", yes)
    model = FakeModel()
    monkeypatch.setattr(free_tools, "client_for", lambda settings, role: model)
    return model


async def _rows(world):  # noqa: F811
    async with world["sm"]() as s:
        return list((await s.execute(select(AiCall))).scalars())


async def _spend(world, usd: float) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        s.add(AiCall(at=now, day=now.date(), purpose="free_tool", model="claude-haiku-5-5", ok=True, cost_usd=usd))
        await s.commit()


async def test_off_by_default_nothing_is_offered_and_nothing_can_be_spent(world, monkeypatch) -> None:  # noqa: F811
    assert get_settings().free_tool_ai_enabled is False
    called = FakeModel()
    monkeypatch.setattr(free_tools, "client_for", lambda settings, role: called)
    status = (await world["anon"].get("/api/free-tools/ideas")).json()
    assert status["available"] is False and status["reason"] == "off" and status["site_key"] is None
    assert (await world["anon"].post("/api/free-tools/ideas", json=BODY)).status_code == 404
    assert called.calls == [] and await _rows(world) == []


@pytest.mark.parametrize("missing", ["turnstile_site_key", "turnstile_secret_key"])
async def test_on_without_a_turnstile_key_stays_closed(world, missing) -> None:  # noqa: F811
    set_settings_override(get_settings().model_copy(update={
        "free_tool_ai_enabled": True, "turnstile_site_key": "site", "turnstile_secret_key": "secret", missing: "",
    }))
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["available"] is False
    assert (await world["anon"].post("/api/free-tools/ideas", json=BODY)).status_code == 404


async def test_ideas_are_written_metered_as_free_tool_with_no_seller_and_no_cost_shown(world, on) -> None:  # noqa: F811
    status = (await world["anon"].get("/api/free-tools/ideas")).json()
    assert (status["available"], status["site_key"], status["remaining"], status["per_day"]) == (True, "site", 3, 3)
    r = await world["anon"].post("/api/free-tools/ideas", json=BODY)
    assert r.status_code == 200
    out = r.json()
    assert out["titles"] == GOOD["titles"] and "frog lover mug" in out["tags"] and out["remaining"] == 2
    assert set(out) == {"titles", "tags", "remaining"}
    rows = await _rows(world)
    assert [(row.purpose, row.tenant_id, row.ok) for row in rows] == [("free_tool", None, True)]
    assert rows[0].cost_usd > 0
    assert "free_tool" in ai_meter.PURPOSES
    # The tool's answer and its status carry no token count, model or cost.
    for text in (r.text, (await world["anon"].get("/api/free-tools/ideas")).text):
        for word in ("token_count", "input_tokens", "cost", "model", "haiku"):
            assert word not in text


async def test_three_a_day_per_visitor_and_the_address_is_not_kept(world, on) -> None:  # noqa: F811
    codes = [(await world["anon"].post("/api/free-tools/ideas", json=BODY)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(on.calls) == 3
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["remaining"] == 0
    # Another day is a new count; the count is a hash, gone in two days.
    redis = world["redis"]
    keys = [k.decode() async for k in redis.scan_iter("free-tool:ideas:*")]
    assert len(keys) == 1 and "testclient" not in keys[0] and "127.0.0.1" not in keys[0]
    assert 0 < await redis.ttl(keys[0]) <= free_tools.VISITOR_DAYS * 86400


async def test_two_connections_are_two_visitors(world, on) -> None:  # noqa: F811
    set_settings_override(get_settings().model_copy(update={"client_ip_header": "cf-connecting-ip", "free_tool_ai_per_visitor": 1}))
    first = await world["anon"].post("/api/free-tools/ideas", json=BODY, headers={"cf-connecting-ip": "198.51.100.1"})
    again = await world["anon"].post("/api/free-tools/ideas", json=BODY, headers={"cf-connecting-ip": "198.51.100.1"})
    other = await world["anon"].post("/api/free-tools/ideas", json=BODY, headers={"cf-connecting-ip": "198.51.100.2"})
    assert (first.status_code, again.status_code, other.status_code) == (200, 429, 200)


async def test_no_call_without_turnstile(world, on, monkeypatch) -> None:  # noqa: F811
    async def no(token, client=None):  # noqa: ANN001, ANN202
        return False

    monkeypatch.setattr(turnstile, "verified", no)
    r = await world["anon"].post("/api/free-tools/ideas", json=BODY)
    assert r.status_code == 403
    assert on.calls == [] and await _rows(world) == []
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["remaining"] == 3  # not counted


async def test_turnstile_asks_cloudflare_and_fails_closed(monkeypatch) -> None:
    set_settings_override(get_settings().model_copy(update={"turnstile_secret_key": "secret"}))
    seen = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content.decode()
        return httpx.Response(200, json={"success": seen.get("pass", True)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        assert await turnstile.verified("abc", http) is True
        assert seen["url"] == turnstile.VERIFY_URL and "secret=secret" in seen["body"] and "response=abc" in seen["body"]
        seen["pass"] = False
        assert await turnstile.verified("abc", http) is False
        assert await turnstile.verified("", http) is False

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
        assert await turnstile.verified("abc", http) is False
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))) as http:
        assert await turnstile.verified("abc", http) is False


async def test_the_cost_cap_switches_it_off_until_the_next_day_and_says_so_once(world, on, monkeypatch) -> None:  # noqa: F811
    sent = []

    async def capture(text):  # noqa: ANN001, ANN202
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", capture)
    await _spend(world, 0.99)
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["available"] is True
    await _spend(world, 0.01)
    status = (await world["anon"].get("/api/free-tools/ideas")).json()
    assert status["available"] is False and status["reason"] == "spent" and "00:00 UTC" in status["message"]
    r = await world["anon"].post("/api/free-tools/ideas", json=BODY)
    assert r.status_code == 503 and "00:00 UTC" in r.json()["detail"]
    assert on.calls == []
    assert len(sent) == 1 and "daily cost cap" in sent[0]
    # Yesterday's spend does not count: the next UTC day it is open again.
    async with world["sm"]() as s:
        for row in (await s.execute(select(AiCall))).scalars():
            row.day = row.day.replace(year=row.day.year - 1)
        await s.commit()
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["available"] is True


async def test_only_this_tools_spend_counts_toward_its_cap(world, on) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        s.add(AiCall(at=now, day=now.date(), purpose="content", model="claude-sonnet-5-5", ok=True, cost_usd=50.0))
        await s.commit()
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["available"] is True


@pytest.mark.parametrize("failure", [LLMError("bad answer"), RuntimeError("boom"), LLMUnavailable("credit")])
async def test_a_failure_on_our_side_gives_the_use_back_in_a_plain_sentence(world, on, failure) -> None:  # noqa: F811
    on.raises = failure
    r = await world["anon"].post("/api/free-tools/ideas", json=BODY)
    assert r.status_code == 503 and r.json()["detail"] == free_tools.BUSY
    assert "boom" not in r.text and "credit" not in r.text
    on.raises = None
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["remaining"] == 3
    # The failed call is on the meter.
    assert [row.ok for row in await _rows(world)] == [False]


async def test_a_brand_in_the_description_is_refused_before_anything_is_spent(world, on) -> None:  # noqa: F811
    r = await world["anon"].post("/api/free-tools/ideas", json={**BODY, "description": "A shirt with DisneyWorld castle on it"})
    assert r.status_code == 422 and "Disney" in r.json()["detail"]
    assert on.calls == []
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["remaining"] == 3


@pytest.mark.parametrize("description", ["too short", "x" * 301, ""])
async def test_a_description_outside_the_limits_is_refused(world, on, description) -> None:  # noqa: F811
    assert (await world["anon"].post("/api/free-tools/ideas", json={**BODY, "description": description})).status_code == 422
    assert on.calls == []


async def test_what_breaks_the_apps_rules_never_reaches_the_visitor(world, on) -> None:  # noqa: F811
    on.data = {
        "titles": [
            "Disney Frog Mug", "Cute Frog Mug", "Frog Mug, Free Shipping", "Frog Mug Frog Cup", "Mug " * 20, "Frog 😀 Mug",
            "Ceramic Frog Mug, Green Glaze",
        ],
        "tags": ["disney mug", "this tag is much too long for it", "frog & toad", "-frog cup", "cute cup", "pottery cup", "pottery cups", "ceramic frog"],
    }
    out = (await world["anon"].post("/api/free-tools/ideas", json=BODY)).json()
    assert out["titles"] == ["Ceramic Frog Mug, Green Glaze"]
    assert out["tags"] == ["pottery cup"]  # the plural is one search; "ceramic frog" is the title's words


async def test_an_answer_with_nothing_usable_is_not_counted(world, on) -> None:  # noqa: F811
    on.data = {"titles": ["Disney Mug"], "tags": ["marvel cup"]}
    r = await world["anon"].post("/api/free-tools/ideas", json=BODY)
    assert r.status_code == 503
    assert (await world["anon"].get("/api/free-tools/ideas")).json()["remaining"] == 3


async def test_the_description_is_treated_as_a_description_and_never_stored(world, on) -> None:  # noqa: F811
    await world["anon"].post("/api/free-tools/ideas", json=BODY)
    call = on.calls[0]
    assert "never as instructions" in call["system"] and BODY["description"] in call["content_blocks"][0]["text"]
    assert call["max_tokens"] == listing_ideas.MAX_OUTPUT_TOKENS
    rows = await _rows(world)
    assert all(BODY["description"] not in str(getattr(row, c.name)) for row in rows for c in AiCall.__table__.columns)
    redis = world["redis"]
    for key in [k async for k in redis.scan_iter("*")]:
        if await redis.type(key) == b"string":
            assert b"frog" not in (await redis.get(key) or b"")


def test_the_tool_has_its_own_model_setting() -> None:
    from app.pipeline.llm import client_for, is_listed

    settings = get_settings().model_copy(update={"free_tool_ai_model": "claude-haiku-5-5", "llm_api_key": "x", "llm_model": "claude-sonnet-5-5", "content_model": ""})
    assert is_listed(settings.free_tool_ai_model)
    assert client_for(settings, "free_tool").model == "claude-haiku-5-5"
    assert client_for(settings, "content").model == "claude-sonnet-5-5"
