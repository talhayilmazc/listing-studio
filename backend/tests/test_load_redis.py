"""What the profiler found in the first minutes of 100 sellers (docs/load-test.md), held in place.

* Redis commands of a process do not wait for each other (the pool's lock, core/redis_pool.py);
* reading a session takes one round trip, and touching it one more, once a minute;
* a model client and a prompt template are made once;
* only so many uploaded files are in memory at once.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio.connection import ConnectionPool

from app.core import image_work, redis_pool
from app.core.sessions import SessionStore, TOUCH_EVERY, _session_key, _tenant_key
from app.pipeline import llm
from app.pipeline.templates import load_template

APP = Path(__file__).resolve().parents[1] / "app"


class _SlowCheckConnection:
    """As the real one: being checked takes a turn of the event loop."""

    def __init__(self, **_: Any) -> None:
        self.pid = 0

    async def connect(self) -> None:
        return None

    async def re_auth(self) -> None:
        return None

    async def can_read_destructive(self) -> bool:
        await asyncio.sleep(0.02)
        return False


async def test_many_commands_at_once_do_not_wait_for_each_others_check() -> None:
    redis_pool.install()
    pool = ConnectionPool(connection_class=_SlowCheckConnection)
    started = time.perf_counter()
    connections = await asyncio.gather(*(pool.get_connection() for _ in range(50)))
    took = time.perf_counter() - started
    assert len({id(c) for c in connections}) == 50, "each has its own connection"
    assert took < 0.5, f"one after another this takes a second; it took {took:.2f} s"


async def test_a_connection_that_fails_its_check_goes_back() -> None:
    class Broken(_SlowCheckConnection):
        async def can_read_destructive(self) -> bool:
            raise ConnectionError("gone")

        async def disconnect(self) -> None:
            return None

    redis_pool.install()
    pool = ConnectionPool(connection_class=Broken)
    with pytest.raises(ConnectionError):
        await pool.get_connection()
    assert not pool._in_use_connections


def test_every_process_installs_it() -> None:
    assert "redis_pool.install()" in (APP / "api" / "deps.py").read_text(encoding="utf-8")
    assert "redis_pool.install()" in (APP / "workers" / "settings.py").read_text(encoding="utf-8")


# -- a session -----------------------------------------------------------------------------------


class _Counting(FakeAsyncRedis):
    pipelines = 0

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        _Counting.pipelines += 1
        return super().pipeline(*args, **kwargs)


async def test_a_session_read_touches_it_once_a_minute() -> None:
    import uuid

    redis = _Counting()
    _Counting.pipelines = 0
    store = SessionStore(redis, ttl_seconds=3600)
    tenant = uuid.uuid4()
    token = await store.create(tenant)
    key = _session_key(token)

    await redis.expire(key, 100)
    assert (await store.read(token)).tenant_id == tenant
    assert await redis.ttl(key) == 100, "just made: not touched again"
    assert _Counting.pipelines == 0

    old = datetime.now(timezone.utc) - TOUCH_EVERY - timedelta(seconds=1)
    await redis.hset(key, "last_seen", old.isoformat())
    assert (await store.read(token)).tenant_id == tenant
    assert await redis.ttl(key) > 100, "a minute on: the expiry slides"
    assert await redis.ttl(_tenant_key(tenant)) > 100
    assert _Counting.pipelines == 1, "in one round trip"

    assert await redis.hget(key, "last_seen") != old.isoformat().encode()


async def test_touch_false_still_touches_nothing() -> None:
    import uuid

    redis = _Counting()
    store = SessionStore(redis, ttl_seconds=3600)
    token = await store.create(uuid.uuid4())
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    await redis.hset(_session_key(token), "last_seen", old.isoformat())
    record = await store.read(token, touch=False)
    assert record.last_seen == old


# -- made once -----------------------------------------------------------------------------------


class _Settings:
    llm_api_key = "test-key"
    llm_model = "claude-sonnet-5-5"
    vision_model = ""
    content_model = "claude-haiku-5-5"
    llm_thinking = "off"
    llm_effort = ""


async def test_a_model_client_is_made_once_per_model() -> None:
    assert llm.client_for(_Settings, "vision") is llm.client_for(_Settings, "vision")
    assert llm.client_for(_Settings, "vision").model == "claude-sonnet-5-5"
    assert llm.client_for(_Settings, "content").model == "claude-haiku-5-5", "each role keeps its model"


async def test_another_key_or_model_is_another_client() -> None:
    class Other(_Settings):
        llm_api_key = "another-key"

    assert llm.client_for(Other, "vision") is not llm.client_for(_Settings, "vision")


def test_a_client_is_not_shared_across_event_loops() -> None:
    """Its connections belong to the loop that opened them."""

    async def one() -> Any:
        return llm.client_for(_Settings, "vision")

    assert asyncio.run(one()) is not asyncio.run(one())


def test_a_template_is_read_once(tmp_path: Path) -> None:
    folder = tmp_path / "content" / "x"
    folder.mkdir(parents=True)
    (folder / "system.md").write_text("system", encoding="utf-8")
    (folder / "user.md").write_text("user $a", encoding="utf-8")
    first = load_template("content/x", tmp_path)
    (folder / "system.md").unlink()
    assert load_template("content/x", tmp_path) is first
    assert first.system == "system"


# -- uploaded files in memory --------------------------------------------------------------------


async def test_only_so_many_uploads_are_in_memory_at_once() -> None:
    from app.core.config import get_settings

    limit = 2 * get_settings().image_work_threads
    inside = most = done = 0

    async def upload() -> None:
        nonlocal inside, most, done
        turn = image_work.upload_turn()
        await turn.__anext__()
        inside += 1
        most = max(most, inside)
        await asyncio.sleep(0.005)
        inside -= 1
        done += 1
        with pytest.raises(StopAsyncIteration):
            await turn.__anext__()

    await asyncio.gather(*(upload() for _ in range(20)))
    assert done == 20 and most == limit


def test_each_route_that_reads_a_whole_file_takes_a_turn_before_its_session() -> None:
    for module, names in (("batches", ("add_asset", "add_archive")),
                          ("mockups", ("add_photo", "add_design", "add_design_archive", "set_design_back"))):
        source = (APP / "api" / f"{module}.py").read_text(encoding="utf-8")
        for name in names:
            body = source[source.index(f"async def {name}("):]
            head = body[:body.index(") ->")]
            assert "upload_turn" in head, name
            assert head.index("upload_turn") < head.index("get_session"), f"{name} would hold its session while it waits"
