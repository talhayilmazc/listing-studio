"""Every alert rule, the grouping, the hourly cap, "resolved", and the digest (docs/monitoring.md)."""

from __future__ import annotations

import json
import shutil
import uuid
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.api.auth import BINDING_COOKIE
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    GroupPlan,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
    Asset,
    AssetStatus,
)
from app.monitor import connections, digest, notify, rules, signals
from app.monitor.notify import Problem
from app.monitor.tick import tick
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from tests.test_oauth import auth_client  # noqa: F401 - a fixture

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_counters():
    signals.reset()
    yield
    signals.reset()


class Inbox:
    def __init__(self, delivered: bool = True) -> None:
        self.messages: list[str] = []
        self.delivered = delivered

    async def __call__(self, text: str) -> bool:
        if self.delivered:
            self.messages.append(text)
        return self.delivered


async def _shop(s, tenant_id, name: str = "Frost Tees", status: ConnectionStatus = ConnectionStatus.active) -> EtsyConnection:
    shop = EtsyConnection(tenant_id=tenant_id, shop_id=abs(hash(name)) % 10_000, shop_name=name, status=status,
                          refresh_token_enc=b"x", access_token_enc=b"y")
    s.add(shop)
    await s.flush()
    return shop


async def _listing(s, tenant_id) -> GeneratedContent:
    batch = UploadBatch(tenant_id=tenant_id, status=UploadBatchStatus.ready, file_count=1)
    s.add(batch)
    await s.flush()
    asset = Asset(batch_id=batch.id, tenant_id=tenant_id, original_filename="a.png", storage_key="k", status=AssetStatus.processed, rank=1)
    s.add(asset)
    await s.flush()
    content = GeneratedContent(tenant_id=tenant_id, batch_id=batch.id, asset_id=asset.id, approved=True, title="Retro Tee",
                               tags=["t"], description="d")
    s.add(content)
    await s.flush()
    return content


async def _keys(sm, redis, now=NOW) -> dict[str, Problem]:
    async with sm() as s:
        return {p.key: p for p in await rules.problems(s, redis, now)}


# --- the rules --------------------------------------------------------------------------------


async def test_a_failed_go_live_is_a_problem_until_it_goes_live(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, tenant)
        content = await _listing(s, tenant)
        s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.publish_live, status=JobStatus.failed,
                  payload={"content_id": str(content.id)}, last_error="Etsy refused it: no shipping", finished_at=NOW - timedelta(hours=1)))
        pub = ListingPublication(tenant_id=tenant, content_id=content.id, connection_id=shop.id, etsy_listing_id=5, state="draft")
        s.add(pub)
        await s.commit()
    found = await _keys(async_sm, fake_redis)
    problem = found[f"golive_failed:{tenant}"]
    assert "s***@example.com" in problem.line and "seller@example.com" not in problem.line
    assert "1 go-live failed" in problem.line and "Etsy refused it: no shipping" in problem.line and "Frost Tees" in problem.line
    async with async_sm() as s:
        (await s.execute(select(ListingPublication))).scalar_one().state = "active"
        await s.commit()
    assert f"golive_failed:{tenant}" not in await _keys(async_sm, fake_redis)


async def test_a_go_live_refused_by_our_own_check_counts_too(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, tenant)
        content = await _listing(s, tenant)
        s.add(ListingPublication(tenant_id=tenant, content_id=content.id, connection_id=shop.id, etsy_listing_id=5, state="draft",
                                 scheduled_for=NOW - timedelta(hours=1), schedule_note="Not published: the shop is no longer connected."))
        await s.commit()
    assert "no longer connected" in (await _keys(async_sm, fake_redis))[f"golive_failed:{tenant}"].line


async def test_a_missed_slot_is_a_problem_unless_the_seller_paused_it(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, tenant)
        content = await _listing(s, tenant)
        plan = GroupPlan(tenant_id=tenant, batch_id=content.batch_id, settings={})
        s.add(plan)
        await s.flush()
        s.add(PlannedSlot(tenant_id=tenant, plan_id=plan.id, content_id=content.id, connection_id=shop.id,
                          draft_at=NOW - timedelta(hours=3), publish_at=NOW - timedelta(minutes=11), state="waiting"))
        await s.commit()
    assert "1 scheduled listing passed its time" in (await _keys(async_sm, fake_redis))[f"missed_slot:{tenant}"].line
    assert f"missed_slot:{tenant}" not in await _keys(async_sm, fake_redis, NOW - timedelta(minutes=2))  # not yet late
    async with async_sm() as s:
        (await s.get(Tenant, tenant)).schedules_paused = True
        await s.commit()
    assert f"missed_slot:{tenant}" not in await _keys(async_sm, fake_redis)


async def test_a_job_stuck_over_half_an_hour_is_a_problem_but_one_waiting_for_its_time_or_the_seller_is_not(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, tenant)
        s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.running, payload={},
                  started_at=NOW - timedelta(minutes=29)))
        s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.queued, payload={},
                  scheduled_at=NOW + timedelta(hours=9), paused_reason="daily budget"))
        s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.queued, payload={},
                  created_at=NOW - timedelta(days=2), paused_reason="personalization_held"))
        await s.commit()
    assert f"job_stuck:{tenant}" not in await _keys(async_sm, fake_redis)
    async with async_sm() as s:
        s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.publish_live, status=JobStatus.running, payload={},
                  started_at=NOW - timedelta(minutes=31)))
        await s.commit()
    line = (await _keys(async_sm, fake_redis))[f"job_stuck:{tenant}"].line
    assert "1 job not moving (1 publish_live), the oldest for 31 minutes" in line


async def test_a_shop_whose_token_stopped_working_is_a_problem_and_a_shop_the_seller_disconnected_is_not(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        gone = await _shop(s, tenant, "Harbor", ConnectionStatus.expired)
        await _shop(s, tenant, "Left", ConnectionStatus.revoked)
        await s.commit()
    found = await _keys(async_sm, fake_redis)
    assert "Harbor" in found[f"token:{tenant}"].line and "Left" not in found[f"token:{tenant}"].line
    assert len([k for k in found if k.startswith("token:")]) == 1
    _ = gone


async def test_five_hundreds_over_two_percent_in_ten_minutes(fake_redis, async_sm) -> None:
    stamp = NOW.timestamp()
    for n in range(100):
        signals.request_done("/api/batches", 200, 0.1, now=stamp - 60)
    signals.request_done("/api/batches", 500, 0.1, now=stamp - 60)
    signals.request_done("/api/batches", 500, 0.1, now=stamp)
    await signals.flush(fake_redis, "api", now=stamp)
    assert "api_5xx" not in await _keys(async_sm, fake_redis)  # 2 of 102 is under 2%
    signals.request_done("/api/batches", 500, 0.1, now=stamp)
    await signals.flush(fake_redis, "api", now=stamp)
    assert "3 of 103 requests (2.9%)" in (await _keys(async_sm, fake_redis))["api_5xx"].line
    later = NOW + timedelta(minutes=11)
    assert "api_5xx" not in await _keys(async_sm, fake_redis, later)


async def test_a_few_errors_on_almost_no_traffic_are_not_a_share_of_anything(fake_redis, async_sm) -> None:
    signals.request_done("/api/batches", 500, 0.1, now=NOW.timestamp())
    await signals.flush(fake_redis, "api", now=NOW.timestamp())
    assert "api_5xx" not in await _keys(async_sm, fake_redis)


@pytest.mark.parametrize(("key", "name"), [(signals.WORKER_BEAT, "worker"), (signals.SCHEDULER_BEAT, "scheduler")])
async def test_a_heartbeat_older_than_five_minutes(fake_redis, async_sm, key: str, name: str) -> None:
    other = signals.SCHEDULER_BEAT if key == signals.WORKER_BEAT else signals.WORKER_BEAT
    await signals.beat(fake_redis, other, now=NOW.timestamp())
    await signals.beat(fake_redis, key, now=NOW.timestamp() - 299)
    assert f"beat:{name}" not in await _keys(async_sm, fake_redis)
    await signals.beat(fake_redis, key, now=NOW.timestamp() - 301)
    assert "5 minutes ago" in (await _keys(async_sm, fake_redis))[f"beat:{name}"].line


async def test_a_worker_that_never_started_is_noticed_after_five_minutes(fake_redis, async_sm) -> None:
    assert "beat:worker" not in await _keys(async_sm, fake_redis)  # the very first look
    assert "beat:worker" in await _keys(async_sm, fake_redis, NOW + timedelta(minutes=6))


async def test_database_connections_above_eighty_percent_of_the_pool(fake_redis, async_sm) -> None:
    signals.pool_in_use(12, 15)
    await signals.flush(fake_redis, "api", now=NOW.timestamp())
    assert "pool:api" not in await _keys(async_sm, fake_redis)  # exactly 80%
    signals.pool_in_use(13, 15)
    await signals.flush(fake_redis, "api", now=NOW.timestamp())
    assert "13 of its 15" in (await _keys(async_sm, fake_redis))["pool:api"].line


async def test_the_idle_transaction_timeout_firing(fake_redis, async_sm) -> None:
    from types import SimpleNamespace

    from app.db.session import _say_connection_lost

    _say_connection_lost(SimpleNamespace(is_disconnect=True, original_exception=OSError("closed"), statement="SELECT 1"))
    await signals.flush(fake_redis, "api")
    assert "idle_timeout" in await _keys(async_sm, fake_redis, datetime.now(timezone.utc))


async def test_disk_above_85_percent(fake_redis, async_sm, monkeypatch) -> None:
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(100, 85, 15))
    assert "disk" not in await _keys(async_sm, fake_redis)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(100, 86, 14))
    assert "86%" in (await _keys(async_sm, fake_redis))["disk"].line


def _containers(at: float, **worker) -> str:
    row = {"name": "worker", "id": "w1", "mem_mb": 1000, "limit_mb": 2460, "restarts": 0, "oom": False, **worker}
    return json.dumps({"at": at, "containers": [row]})


async def test_container_memory_above_ninety_percent_of_its_limit(fake_redis, async_sm) -> None:
    await fake_redis.set(rules.CONTAINERS, _containers(NOW.timestamp(), mem_mb=2214))
    assert "memory:worker" not in await _keys(async_sm, fake_redis)
    await fake_redis.set(rules.CONTAINERS, _containers(NOW.timestamp(), mem_mb=2215))
    assert "2215 MB of its 2460 MB" in (await _keys(async_sm, fake_redis))["memory:worker"].line


async def test_a_restart_or_oom_kill_is_a_problem_and_a_deploys_new_container_is_not(fake_redis, async_sm) -> None:
    stamp = NOW.timestamp()
    await fake_redis.set(rules.CONTAINERS, _containers(stamp, restarts=2))
    assert "restart:worker" not in await _keys(async_sm, fake_redis)  # the first we hear of it: a baseline
    await fake_redis.set(rules.CONTAINERS, _containers(stamp, id="w2", restarts=0))  # a deploy
    assert "restart:worker" not in await _keys(async_sm, fake_redis)
    await fake_redis.set(rules.CONTAINERS, _containers(stamp, id="w2", restarts=1))
    assert "restarted (1 times" in (await _keys(async_sm, fake_redis))["restart:worker"].line
    await fake_redis.delete(rules.RESTART_EVENTS)
    await fake_redis.set(rules.CONTAINERS, _containers(stamp, id="w2", restarts=1, oom=True))
    assert "OOM" in (await _keys(async_sm, fake_redis))["restart:worker"].line
    await fake_redis.set(rules.CONTAINERS, _containers(stamp + 3700, id="w2", restarts=1))
    assert "restart:worker" not in await _keys(async_sm, fake_redis, NOW + timedelta(seconds=3700))  # stayed up


async def test_the_container_watch_going_quiet_is_a_problem_itself(fake_redis, async_sm, test_settings) -> None:
    await fake_redis.set(rules.CONTAINERS, _containers(NOW.timestamp() - 301))
    assert "watch" in await _keys(async_sm, fake_redis)
    await fake_redis.delete(rules.CONTAINERS)
    assert "watch" not in await _keys(async_sm, fake_redis)  # development: there is no watch
    test_settings.app_env = "production"
    assert "watch" in await _keys(async_sm, fake_redis)


async def test_many_shops_lost_at_once_are_one_alert_for_the_account(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        for name in ("Alpha", "Beta", "Gamma"):
            await _shop(s, tenant, name, ConnectionStatus.expired)
        await s.commit()
    found = [p for p in (await _keys(async_sm, fake_redis)).values() if p.key.startswith("token:")]
    assert len(found) == 1 and "3 shops (Alpha, Beta, Gamma)" in found[0].line


async def test_two_tries_without_a_connection_in_a_day(fake_redis, async_sm) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    stamp = NOW.timestamp()
    await connections.record(fake_redis, connections.ATTEMPT, tenant, now=stamp - 3600)
    assert f"connect_failing:{tenant}" not in await _keys(async_sm, fake_redis)
    await connections.record(fake_redis, connections.ATTEMPT, tenant, now=stamp - 60)
    assert "2 tries to connect a shop" in (await _keys(async_sm, fake_redis))[f"connect_failing:{tenant}"].line
    await connections.record(fake_redis, connections.CONNECTED, tenant, now=stamp)
    assert f"connect_failing:{tenant}" not in await _keys(async_sm, fake_redis, NOW + timedelta(seconds=1))


async def test_two_tries_a_day_apart_are_not_a_problem(fake_redis, async_sm) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    await connections.record(fake_redis, connections.ATTEMPT, tenant, now=NOW.timestamp() - 90000)
    await connections.record(fake_redis, connections.ATTEMPT, tenant, now=NOW.timestamp())
    assert f"connect_failing:{tenant}" not in await _keys(async_sm, fake_redis)


# --- grouping, the cap, resolved --------------------------------------------------------------


def _p(key: str = "a", line: str = "line") -> Problem:
    return Problem(key, "kind", line)


async def test_one_alert_per_problem_until_it_is_fixed_and_then_resolved() -> None:
    redis, inbox = FakeAsyncRedis(), Inbox()
    t = NOW.timestamp()
    assert (await notify.apply(redis, [_p()], inbox, now=t))["alerts"] == 1
    for minute in range(1, 30):
        await notify.apply(redis, [_p(line=f"line now {minute}")], inbox, now=t + minute * 60)
    assert inbox.messages == ["[Listyro] kind: line"]
    # Gone, but not long enough to call it fixed.
    await notify.apply(redis, [], inbox, now=t + 30 * 60)
    assert len(inbox.messages) == 1
    # It came back in that time: the same problem, nothing said.
    await notify.apply(redis, [_p()], inbox, now=t + 35 * 60)
    assert len(inbox.messages) == 1
    assert (await notify.apply(redis, [], inbox, now=t + 46 * 60))["resolved"] == 1
    assert inbox.messages[-1] == "[Listyro] Resolved: kind: line"
    await notify.apply(redis, [], inbox, now=t + 60 * 60)
    assert len(inbox.messages) == 2
    assert await notify.still_open(redis) == []


async def test_a_problem_that_lasts_is_said_again_after_a_day() -> None:
    redis, inbox = FakeAsyncRedis(), Inbox()
    t = NOW.timestamp()
    await notify.apply(redis, [_p()], inbox, now=t)
    await notify.apply(redis, [_p()], inbox, now=t + 86399)
    assert len(inbox.messages) == 1
    await notify.apply(redis, [_p()], inbox, now=t + 86400)
    assert inbox.messages[-1] == "[Listyro] Still: kind: line"


async def test_at_most_twenty_an_hour_and_the_rest_go_in_the_next_digest(async_sm) -> None:
    redis, inbox = FakeAsyncRedis(), Inbox()
    t = NOW.timestamp() - NOW.timestamp() % 3600
    problems = [_p(f"k{n}", f"line {n}") for n in range(25)]
    counts = await notify.apply(redis, problems, inbox, now=t)
    assert counts["alerts"] == 20 and counts["deferred"] == 5 and len(inbox.messages) == 20
    async with async_sm() as s:
        text = await digest.build(s, redis, NOW)
    assert "Kept back by the hourly limit on alerts: 5" in text and "line 24" in text
    # The next hour there is room: they are said then, and no longer kept back.
    await notify.apply(redis, problems, inbox, now=t + 3600)
    assert len(inbox.messages) == 25
    assert await redis.hlen(notify.DEFERRED) == 0


async def test_a_message_that_could_not_be_delivered_is_tried_again() -> None:
    redis, inbox = FakeAsyncRedis(), Inbox(delivered=False)
    t = NOW.timestamp()
    await notify.apply(redis, [_p()], inbox, now=t)
    assert inbox.messages == []
    inbox.delivered = True
    await notify.apply(redis, [_p()], inbox, now=t + 60)
    assert len(inbox.messages) == 1
    await notify.apply(redis, [_p()], inbox, now=t + 120)
    assert len(inbox.messages) == 1


async def test_a_problem_never_said_is_not_resolved_out_loud() -> None:
    redis, inbox = FakeAsyncRedis(), Inbox(delivered=False)
    await notify.apply(redis, [_p()], inbox, now=NOW.timestamp())
    inbox.delivered = True
    await notify.apply(redis, [], inbox, now=NOW.timestamp() + 700)
    assert inbox.messages == [] and await notify.still_open(redis) == []


async def test_one_alert_per_account_not_per_listing(async_sm, fake_redis) -> None:
    tenant = await make_tenant(async_sm, "seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, tenant)
        for n in range(30):
            content = await _listing(s, tenant)
            s.add(Job(tenant_id=tenant, connection_id=shop.id, type=JobType.publish_live, status=JobStatus.failed,
                      payload={"content_id": str(content.id)}, last_error=f"reason {n}", finished_at=NOW - timedelta(hours=1)))
        await s.commit()
    found = [k for k in await _keys(async_sm, fake_redis) if k.startswith("golive_failed")]
    assert found == [f"golive_failed:{tenant}"]


# --- the replay of 2026-10-23: every shop connection refused -----------------------------------


async def test_replay_of_2026_10_23_every_callback_refused_alerts_within_ten_minutes(auth_client, async_sm) -> None:  # noqa: F811
    """A callback that always refused: the browser held no binding, as the session cookie never
    reached the API's host that week. Three sellers try; we know at the first look, once per account."""
    inbox = Inbox()
    sm, redis = auth_client.sm, auth_client.redis
    started = datetime.now(timezone.utc)
    sellers = [auth_client.tenant_id, await make_tenant(sm, "second@example.com"), await make_tenant(sm, "third@example.com")]
    for n, tenant in enumerate(sellers):
        async with httpx.AsyncClient(transport=auth_client._transport, base_url="http://test", follow_redirects=False,
                                     headers=BROWSER_HEADERS) as browser:
            authenticate(browser, await open_session(redis, tenant))
            for _ in range(2):  # each tries twice, and is refused each time
                start = await browser.get("/api/auth/etsy/start")
                assert start.status_code == 302
                state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
                browser.cookies.delete(BINDING_COOKIE)
                cb = await browser.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
                assert cb.headers["location"].endswith("/connect?status=no_binding")
        _ = n
    await tick(sm, redis, now=started + timedelta(minutes=1), send=inbox)
    refused = [m for m in inbox.messages if "shop connection refused" in m]
    assert len(refused) == 3, inbox.messages  # within a minute, one per account, not one per try
    assert all("no_binding" in m and "2 times" in m and "@example.com" in m for m in refused)
    assert not any("owner@example.com" in m or "second@example.com" in m for m in inbox.messages)  # masked
    assert len([m for m in inbox.messages if "shop connection failing" in m]) == 3
    def about_connections() -> list[str]:
        return [m for m in inbox.messages if "shop connection" in m]

    before = len(about_connections())
    for minute in range(2, 11):
        await tick(sm, redis, now=started + timedelta(minutes=minute), send=inbox)
    assert len(about_connections()) == before  # nothing repeated
    # It is fixed: the first seller connects. That account's problems clear, and it is said.
    start = await auth_client.get("/api/auth/etsy/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert "status=connected" in cb.headers["location"]
    await tick(sm, redis, now=datetime.now(timezone.utc) + timedelta(minutes=12), send=inbox)
    await tick(sm, redis, now=datetime.now(timezone.utc) + timedelta(minutes=23), send=inbox)
    resolved = [m for m in inbox.messages if "Resolved" in m and "o***@example.com" in m]
    assert len(resolved) == 2, inbox.messages


# --- the digest -------------------------------------------------------------------------------


async def test_the_digest_says_per_account_and_overall_with_no_address_and_no_token(async_sm, fake_redis) -> None:
    first = await make_tenant(async_sm, "first-seller@example.com")
    quiet = await make_tenant(async_sm, "quiet-seller@example.com")
    async with async_sm() as s:
        shop = await _shop(s, first)
        await _shop(s, quiet, "Quiet Shop")
        done = await _listing(s, first)
        s.add(Job(tenant_id=first, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.failed, payload={},
                  last_error="Etsy refused it: no image", finished_at=NOW - timedelta(hours=2)))
        s.add(Job(tenant_id=first, connection_id=shop.id, type=JobType.publish_live, status=JobStatus.failed,
                  payload={"content_id": str(done.id)}, last_error="Etsy answered 500", finished_at=NOW - timedelta(hours=2)))
        s.add(Job(tenant_id=first, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.queued, payload={},
                  paused_reason="personalization_held", last_error="Personalization question is too long"))
        held = await _listing(s, first)
        held.approved = False
        s.add(ListingPublication(tenant_id=first, content_id=held.id, connection_id=shop.id, etsy_listing_id=7, state="draft",
                                 scheduled_for=NOW + timedelta(hours=5)))
        late = await _listing(s, first)
        s.add(ListingPublication(tenant_id=first, content_id=late.id, connection_id=shop.id, etsy_listing_id=8, state="draft",
                                 scheduled_for=NOW - timedelta(hours=1), etsy_checked_at=NOW - timedelta(hours=1)))
        changed = await _listing(s, first)
        s.add(ListingPublication(tenant_id=first, content_id=changed.id, connection_id=shop.id, etsy_listing_id=9, state="active",
                                 published_by="seller_on_etsy", published_at=NOW - timedelta(hours=3)))
        await s.commit()
    stamp = NOW.timestamp()
    await connections.record(fake_redis, connections.ATTEMPT, first, now=stamp - 60)
    await connections.record(fake_redis, connections.REFUSED, first, "no_binding", now=stamp - 50)
    await connections.record(fake_redis, connections.ATTEMPT, first, now=stamp - 40)
    await connections.record(fake_redis, connections.CONNECTED, first, now=stamp - 30)
    await fake_redis.set("quota:global:2026-11-04", 1234)
    await fake_redis.set("quota:global:2026-11-03", 45678)
    signals.request_done(f"/api/batches/{uuid.uuid4()}", 200, 3.0, now=stamp)
    await signals.flush(fake_redis, "api", now=stamp)
    await fake_redis.set(rules.CONTAINERS, _containers(stamp, mem_mb=1500))
    async with async_sm() as s:
        await rules.problems(s, fake_redis, NOW)
        text = await digest.build(s, fake_redis, NOW)
    for expected in (
        "Daily digest, Wednesday 04 November 2026, 15:00 Istanbul",
        "Accounts with something to look at: 1 of 2",
        "f***@example.com",
        "failed drafts: 1 (last: Etsy refused it: no image)",
        "failed go-lives: 1 (last: Etsy answered 500)",
        "held: 1: Personalization question is too long",
        "missed slots: 1",
        "due in 24 hours and will fail: 1: it is not approved",
        "out of step with Etsy, now followed: 1 published in Shop Manager, 0 deleted there",
        "queue depth: 1",
        "shop connections: 2 tried, 1 succeeded, 1 refused (1: no_binding)",
        "sign-ups: 2",
        "Etsy calls: yesterday 45,678 of ",
        ", today so far 1,234",
        "/api/batches/{id} 3,200 ms (1)",
        "memory peak: worker 1,500 MB",
    ):
        assert expected in text, f"{expected!r} not in:\n{text}"
    assert "quiet-seller" not in text and "q***@example.com" not in text  # nothing to report: counted, not listed
    assert "first-seller@example.com" not in text


async def test_the_digest_goes_at_nine_in_istanbul_once_a_day(async_sm, fake_redis) -> None:
    inbox = Inbox()
    nine = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)  # 09:00 in Istanbul
    await tick(async_sm, fake_redis, now=nine - timedelta(minutes=1), send=inbox)
    assert not any("Daily digest" in m for m in inbox.messages)
    await tick(async_sm, fake_redis, now=nine, send=inbox)
    await tick(async_sm, fake_redis, now=nine + timedelta(minutes=1), send=inbox)
    assert len([m for m in inbox.messages if "Daily digest" in m]) == 1
    await tick(async_sm, fake_redis, now=nine + timedelta(days=1), send=inbox)
    assert len([m for m in inbox.messages if "Daily digest" in m]) == 2


async def test_a_digest_that_could_not_be_delivered_is_tried_again(async_sm, fake_redis) -> None:
    inbox = Inbox(delivered=False)
    nine = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
    await tick(async_sm, fake_redis, now=nine, send=inbox)
    inbox.delivered = True
    await tick(async_sm, fake_redis, now=nine + timedelta(minutes=1), send=inbox)
    assert len([m for m in inbox.messages if "Daily digest" in m]) == 1


def test_the_cli_only_prints_without_send() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("app/ops/health_digest.py").read_text(encoding="utf-8")
    assert "if send else False" in source


# --- each process counts, and the worst is said -------------------------------------------------


async def test_counting_never_breaks_a_request_when_redis_is_down() -> None:
    class Down:
        def pipeline(self, **_k):
            raise ConnectionError("redis is down")

    signals.request_done("/api/batches", 200, 0.1)
    await signals.flush(Down(), "api")  # no exception
    await connections.record(Down(), connections.ATTEMPT, uuid.uuid4())


async def test_what_could_not_be_written_is_written_next_time(fake_redis) -> None:
    class Down:
        def pipeline(self, **_k):
            raise ConnectionError("redis is down")

    t = NOW.timestamp()
    signals.request_done("/api/batches", 500, 0.1, now=t)
    await signals.flush(Down(), "api", now=t)
    await signals.flush(fake_redis, "api", now=t)
    assert await signals.errors_over(fake_redis, 10, t) == (1, 1)


def test_endpoints_are_counted_without_their_ids() -> None:
    assert signals.route_of(f"/api/batches/{uuid.uuid4()}/shops/{uuid.uuid4()}/rows") == "/api/batches/{id}/shops/{id}/rows"
    assert signals.route_of("/api/listings/12345") == "/api/listings/{id}"


# --- one account never sees another's --------------------------------------------------------


async def test_a_seller_never_sees_another_accounts_check_results(fake_redis, async_sm) -> None:
    from app.api.attention import attention
    from app.workers.preflight import save

    a = await make_tenant(async_sm, "a@example.com")
    b = await make_tenant(async_sm, "b@example.com")
    await save(fake_redis, a, uuid.uuid4(), [{"shop": "s", "shop_name": "A Shop", "kind": "token", "line": "A's secret line",
                                              "href": "/connect", "count": 1}])
    async with async_sm() as s:
        for_b = await attention(s, await s.get(Tenant, b), fake_redis)
        for_a = await attention(s, await s.get(Tenant, a), fake_redis)
    assert [i.label for i in for_b.items] == []
    assert [i.label for i in for_a.items] == ["A's secret line"]
