"""Each ends in a clear, visible state with a plain message, and nobody finds out from a customer
(docs/monitoring.md)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.api.auth import BINDING_COOKIE
from app.core.config import Settings
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
)
from app.etsy import connection as connection_module
from app.etsy.oauth import OAuthError
from app.monitor import notify, rules
from app.monitor.tick import tick
from app.workers import plans as plans_worker
from app.workers import preflight, recovery
from app.workers import publish as publish_worker
from app.workers.schedule import release_scheduled_publishes
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from tests.test_end_to_end import _Httpx, _run_drafts, _send_drafts, _world
from tests.test_oauth import auth_client  # noqa: F401 - a fixture
from tests.test_reconcile_etsy import _drafted, _due, _slot

NOW = datetime.now(timezone.utc)


class Inbox:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return True


def _answering(w: dict, rule) -> None:  # noqa: ANN001
    """Etsy answers ``rule(request)`` when it returns a response, as usual otherwise."""
    fake = w["etsy"]
    usual = type(fake).handle

    async def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: ANN001
        answer = rule(request)
        if answer is not None:
            method, path = request.method, request.url.path.removeprefix("/v3/application")
            self.requests.append(f"{method} {path}")
            return answer
        return await usual(self, request)

    import types

    fake.handle = types.MethodType(handle, fake)
    publish_worker.httpx = _Httpx(fake)  # (restored by monkeypatch at the end of the test)


async def _job(w: dict, job_id: str) -> Job:
    async with w["sm"]() as s:
        return await s.get(Job, uuid.UUID(job_id))


async def _attention_labels(w: dict) -> list[str]:
    body = (await w["client"].get("/api/attention")).json()
    return [i["label"] for i in body["items"]]


async def _problems(w: dict) -> dict[str, notify.Problem]:
    async with w["sm"]() as s:
        return {p.key: p for p in await rules.problems(s, w["ctx"]["redis"], datetime.now(timezone.utc))}


def _token_refused(monkeypatch, *, status: int = 400) -> None:
    async def refused(*_a, **_k):  # noqa: ANN002, ANN003
        raise OAuthError(f"token refresh failed (status {status})", "invalid_grant: token revoked", status)

    monkeypatch.setattr(connection_module, "refresh_tokens", refused)


def _expire(w: dict):  # noqa: ANN201
    async def run() -> None:
        async with w["sm"]() as s:
            for shop in (await s.execute(select(EtsyConnection))).scalars():
                shop.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            await s.commit()

    return run()


# --- 1. the token expires in the middle of a run ---------------------------------------------------


async def test_token_expired_mid_run(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    first, second = w["contents"]
    job_id = await _due(w, first)
    await _expire(w)
    _token_refused(monkeypatch)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "failed"
    job = await _job(w, job_id)
    assert "Connect the shop again" in job.last_error and "Shop 01" in job.last_error and "token" not in job.last_error.lower()
    async with w["sm"]() as s:
        shop = await s.get(EtsyConnection, w["shops"][0])
        assert shop.status is ConnectionStatus.expired
    # The seller is told where to fix it, and so are we.
    assert any("Connect" in label and "Shop 01" in label for label in await _attention_labels(w))
    assert [s["name"] for s in (await w["client"].get("/api/shops")).json()["lost"]] == ["Shop 01"]
    assert f"token:{w['tenant']}" in await _problems(w)
    # The next one is not even tried, and says why.
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == second))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    assert (await release_scheduled_publishes(w["ctx"]))["held"] == 1
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == second))).scalar_one()
        assert "no longer connected" in pub.schedule_note
    await w["engine"].dispose()


async def test_a_401_mid_job_and_a_refused_renewal_also_ends_with_reconnect(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    _token_refused(monkeypatch)
    monkeypatch.setattr("app.core.crypto.get_cipher", publish_worker.get_cipher)  # (the renewal makes its own service)
    _answering(w, lambda r: httpx.Response(401, json={"error": "invalid_token"}) if r.method == "PATCH" else None)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "failed"
    assert "reconnect" in (await _job(w, job_id)).last_error.lower()
    async with w["sm"]() as s:
        assert (await s.get(EtsyConnection, w["shops"][0])).status is ConnectionStatus.expired
    await w["engine"].dispose()


async def test_our_own_key_refused_after_a_bad_deploy_does_not_take_every_shop_away(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    await _expire(w)

    async def refused(*_a, **_k):  # noqa: ANN002, ANN003
        raise OAuthError("token refresh failed (status 401)", "invalid_client: unknown key", 401)

    monkeypatch.setattr(connection_module, "refresh_tokens", refused)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "deferred"
    async with w["sm"]() as s:
        assert (await s.get(EtsyConnection, w["shops"][0])).status is ConnectionStatus.active
    await w["engine"].dispose()


async def test_etsy_being_down_when_renewing_is_not_a_lost_shop(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    await _expire(w)
    _token_refused(monkeypatch, status=503)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "deferred"
    job = await _job(w, job_id)
    assert job.status is JobStatus.queued and job.paused_reason == recovery.WAIT_ETSY_DOWN
    async with w["sm"]() as s:
        assert (await s.get(EtsyConnection, w["shops"][0])).status is ConnectionStatus.active
    await w["engine"].dispose()


async def test_reconnecting_clears_it(tmp_path, test_settings: Settings, monkeypatch) -> None:
    from app.core.crypto import get_cipher  # noqa: F401
    from app.etsy.connection import ConnectionService
    from app.etsy.oauth import TokenResponse

    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    async with w["sm"]() as s:
        shop = await s.get(EtsyConnection, w["shops"][0])
        shop.status = ConnectionStatus.expired
        etsy_user = shop.etsy_user_id
        await s.commit()
    assert f"token:{w['tenant']}" in await _problems(w)
    from cryptography.fernet import Fernet

    from app.core.crypto import TokenCipher

    async with w["sm"]() as s:
        await ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="t").save_from_tokens(
            s, w["tenant"], __import__("app.etsy.oauth", fromlist=["x"]).TokenResponse(
                access_token=f"{etsy_user}.new", refresh_token="r", expires_in=3600), ["shops_r"])
        await s.commit()
    _ = TokenResponse
    assert f"token:{w['tenant']}" not in await _problems(w)
    assert not any("Connect" in label for label in await _attention_labels(w))
    await w["engine"].dispose()


# --- 2. a shop disconnected with slots waiting ------------------------------------------------------


async def test_shop_disconnected_with_pending_slots(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    first, second = w["contents"]
    await _slot(w, first)
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == second))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) + timedelta(hours=2)
        await s.commit()
    res = await w["client"].post(f"/api/shops/{w['shops'][0]}/disconnect")
    assert res.status_code == 200, res.text
    told = res.json()["disconnected"]
    assert told["planned_drafts"] == 1 and told["scheduled_publishes"] == 1
    async with w["sm"]() as s:
        assert {x.state for x in (await s.execute(select(PlannedSlot))).scalars()} == {"cancelled"}
    # The seller chose it: not an alert, and nothing is left "missed".
    found = await _problems(w)
    assert not [k for k in found if k.startswith(("token:", "missed_slot:", "job_stuck:"))]
    await w["engine"].dispose()


# --- 3. the profile is deleted ----------------------------------------------------------------------


async def test_profile_deleted_with_a_slot_waiting(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [content] = w["contents"]
    slot_id = await _slot(w, content)
    async with w["sm"]() as s:
        slot = await s.get(PlannedSlot, slot_id)
        slot.draft_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    res = await w["client"].delete(f"/api/profiles/{w['profile']}")
    assert res.status_code in (200, 204), res.text
    await plans_worker.release_planned_drafts(w["ctx"])
    jobs = [a[1] for a in w["enqueued"] if a[0] == "run_publish_job"]
    for job_id in jobs:
        await publish_worker.run_publish_job(w["ctx"], job_id)
    await plans_worker.release_planned_drafts(w["ctx"])  # the next minute's look
    async with w["sm"]() as s:
        slot = await s.get(PlannedSlot, slot_id)
    assert slot.state == "failed", slot.state
    assert "profile" in (slot.note or "").lower()
    item = (await w["client"].get("/api/scheduled")).json()
    assert any("profile" in (row.get("note") or "").lower() for row in item["items"])
    assert not w["etsy"].listings  # nothing half made on Etsy
    await w["engine"].dispose()


async def test_a_draft_already_on_etsy_still_goes_live_when_its_profile_is_deleted(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    assert (await w["client"].delete(f"/api/profiles/{w['profile']}")).status_code in (200, 204)
    job_id = await _due(w, content)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    await w["engine"].dispose()


# --- 4. a shipping profile deleted on Etsy ----------------------------------------------------------


GONE = "Could not find shipping_profile_id='155' associated with shop"


async def test_shipping_profile_deleted_on_etsy(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    _answering(w, lambda r: httpx.Response(404, json={"error": GONE}) if r.method == "POST" and r.url.path.endswith("/listings") else None)
    [job_id] = await _send_drafts(w)
    assert await publish_worker.run_publish_job(w["ctx"], job_id) == "failed"
    message = (await _job(w, job_id)).last_error
    assert "shipping profile" in message and "Shop 01" in message
    assert not w["etsy"].listings
    assert any("failed draft" in label for label in await _attention_labels(w))
    await w["engine"].dispose()


async def test_the_check_an_hour_before_finds_it_and_says_how_to_fix_it(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=55)
        await s.commit()
    _empty_settings(w, monkeypatch)
    assert await preflight.preflight_scheduled(w["ctx"]) == 1
    [shop] = [a[1] for a in w["enqueued"] if a[0] == "preflight_shop"]
    assert (await preflight.preflight_shop(w["ctx"], shop)).startswith("checked:")
    before = len(w["etsy"].requests)
    assert before and all(r.startswith("GET") for r in w["etsy"].requests[-4:])
    labels = await _attention_labels(w)
    assert any("shipping profile" in label and 'with "change"' in label for label in labels), labels
    item = next(i for i in (await w["client"].get("/api/attention")).json()["items"] if "shipping" in i["label"])
    assert item["href"] == "/profiles"
    inbox = Inbox()
    await tick(w["sm"], w["ctx"]["redis"], now=datetime.now(timezone.utc), send=inbox)
    assert any("scheduled run will fail" in m for m in inbox.messages)
    # Once per shop per hour: the next look asks Etsy nothing.
    w["enqueued"].clear()
    assert await preflight.preflight_scheduled(w["ctx"]) == 0
    await w["engine"].dispose()


def _check_with(w: dict, monkeypatch) -> None:
    monkeypatch.setattr(preflight, "httpx", _Httpx(w["etsy"]))
    monkeypatch.setattr(preflight, "_connection_service", lambda settings: connection_module.ConnectionService(
        publish_worker.get_cipher(), client_id="k", token_url="t"))


def _empty_settings(w: dict, monkeypatch) -> None:
    _answering(w, lambda r: httpx.Response(200, json={"count": 0, "results": []}) if r.method == "GET" and any(
        r.url.path.endswith(end) for end in ("/shipping-profiles", "/policies/return", "/readiness-state-definitions", "/production-partners")) else None)
    _check_with(w, monkeypatch)


async def test_the_check_says_nothing_when_all_is_well(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=55)
        profile = await s.get(ListingProfile, w["profile"])
        ids = profile.cached_payload
        await s.commit()
    rows = {"/shipping-profiles": {"shipping_profile_id": ids.get("shipping_profile_id")},
            "/policies/return": {"return_policy_id": ids.get("return_policy_id")},
            "/readiness-state-definitions": {"readiness_state_id": ids.get("readiness_state_id")},
            "/production-partners": {"production_partner_id": 1}}

    def listing(r: httpx.Request):  # noqa: ANN202
        for end, row in rows.items():
            if r.method == "GET" and r.url.path.endswith(end):
                return httpx.Response(200, json={"count": 1, "results": [row]})
        return None

    _answering(w, listing)
    _check_with(w, monkeypatch)
    await preflight.preflight_scheduled(w["ctx"])
    assert await preflight.preflight_shop(w["ctx"], str(w["shops"][0])) == "checked:0"
    assert await _attention_labels(w) == []
    await w["engine"].dispose()


async def test_the_check_tells_a_lost_token_without_any_read(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=55)
        await s.commit()
    await _expire(w)
    _token_refused(monkeypatch)
    _check_with(w, monkeypatch)
    before = len(w["etsy"].requests)
    await preflight.preflight_scheduled(w["ctx"])
    assert await preflight.preflight_shop(w["ctx"], str(w["shops"][0])) == "token"
    assert len(w["etsy"].requests) == before
    labels = await _attention_labels(w)
    assert any("Connect" in label for label in labels), labels
    await w["engine"].dispose()


async def test_a_disconnected_shop_with_a_run_coming_is_said_without_asking_etsy(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=55)
        (await s.get(EtsyConnection, w["shops"][0])).status = ConnectionStatus.revoked
        await s.commit()
    assert await preflight.preflight_scheduled(w["ctx"]) == 0
    assert any("is not connected" in label for label in await _attention_labels(w))
    await w["engine"].dispose()


# --- 5. Etsy answers 429 or 5xx during a go-live ------------------------------------------------------


@pytest.mark.parametrize(("status", "headers"), [(503, {}), (429, {"Retry-After": "0"})])
async def test_a_passing_429_or_5xx_during_a_go_live_is_retried_and_it_goes_live(tmp_path, test_settings: Settings, monkeypatch, status, headers) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    left = {"n": 2}

    def rule(request: httpx.Request):  # noqa: ANN202
        if request.method == "PATCH" and left["n"]:
            left["n"] -= 1
            return httpx.Response(status, headers=headers, json={"error": "busy"})
        return None

    _answering(w, rule)
    outcome = await publish_worker.run_publish_live_job(w["ctx"], job_id)
    for _ in range(3):
        if outcome != "deferred":
            break
        # It waits a little and runs again by itself, saying what for.
        assert (await _job(w, job_id)).paused_reason == recovery.WAIT_ETSY_DOWN
        outcome = await publish_worker.run_publish_live_job(w["ctx"], job_id)
    assert outcome == "succeeded"
    assert {d["state"] for d in w["etsy"].listings.values()} == {"active"}
    await w["engine"].dispose()


async def test_etsy_down_during_a_go_live_ends_in_a_clear_failure_after_its_tries(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    _answering(w, lambda r: httpx.Response(503, json={"error": "down"}) if r.method == "PATCH" else None)
    outcomes = []
    for _ in range(6):
        outcomes.append(await publish_worker.run_publish_live_job(w["ctx"], job_id))
        job = await _job(w, job_id)
        if job.status is JobStatus.failed:
            break
        assert job.paused_reason == recovery.WAIT_ETSY_DOWN  # waiting, and it says what for
    assert job.status is JobStatus.failed, outcomes
    assert "Etsy" in job.last_error and "Nothing is wrong with the listing" in job.last_error
    assert {d["state"] for d in w["etsy"].listings.values()} == {"draft"}
    found = await _problems(w)
    assert f"golive_failed:{w['tenant']}" in found
    await w["engine"].dispose()


async def test_etsy_saying_429_for_long_during_a_go_live_ends_clearly(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    _answering(w, lambda r: httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow"}) if r.method == "PATCH" else None)
    for _ in range(8):
        await publish_worker.run_publish_live_job(w["ctx"], job_id)
        job = await _job(w, job_id)
        if job.status is JobStatus.failed:
            break
    assert job.status is JobStatus.failed
    assert "too fast" in job.last_error
    await w["engine"].dispose()


# --- 6. the same listing scheduled twice -------------------------------------------------------------


async def test_the_same_listing_scheduled_twice_has_one_time_and_goes_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    shop = w["shops"][0]
    first = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    second = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    for when in (first, second):
        res = await w["client"].post("/api/schedules", json={"items": [{"content_id": str(content), "connection_id": str(shop), "run_at": when}]})
        assert res.status_code == 200, res.text
    listed = (await w["client"].get("/api/schedules")).json()
    assert len(listed) == 1
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalar_one()
        pub.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 1
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 0  # the next minute's run
    await w["engine"].dispose()


async def test_a_batch_scheduled_twice_from_two_tabs_is_scheduled_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(), "per_shop_per_day": 5,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    a = await w["client"].post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    b = await w["client"].post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert a.status_code == 200 and a.json()["scheduled"] == 2
    assert b.status_code in (200, 409) and (b.status_code == 409 or not b.json().get("scheduled"))
    async with w["sm"]() as s:
        slots = list((await s.execute(select(PlannedSlot).where(PlannedSlot.state != "cancelled"))).scalars())
    assert len(slots) == 2
    await w["engine"].dispose()


# --- 7. two tabs approving and sending at once ----------------------------------------------------------


def _scratch_postgres() -> str:
    """A scratch database on the development server, or skip. Synthetic data only."""
    import sqlalchemy as sa

    from app.core.config import get_settings

    url = sa.engine.make_url(get_settings().database_url.replace("+asyncpg", "+psycopg"))
    if url.get_backend_name() != "postgresql":
        pytest.skip("no Postgres configured")
    name = "listyro_hard_cases"
    try:
        admin = sa.create_engine(url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT",
                                 connect_args={"connect_timeout": 2})
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(sa.text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
    except Exception as exc:  # noqa: BLE001 - no server here
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    return url.set(database=name).render_as_string(hide_password=False).replace("+psycopg", "+asyncpg")


def _drop_scratch_postgres(url: str) -> None:
    import sqlalchemy as sa

    from app.core.config import get_settings

    parsed = sa.engine.make_url(url)
    admin = sa.create_engine(sa.engine.make_url(get_settings().database_url.replace("+asyncpg", "+psycopg")).render_as_string(hide_password=False),
                             isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
    admin.dispose()


async def test_two_tabs_approving_and_sending_at_once_make_one_draft_each(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Really at once, on Postgres: on SQLite's one shared connection the two would only trip over it."""
    url = _scratch_postgres()
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=3, database_url=url)
    c = w["client"]
    async with w["sm"]() as s:
        for row in (await s.execute(select(GeneratedContent))).scalars():
            row.approved = False
        await s.commit()
    one, two = await asyncio.gather(c.post(f"/api/batches/{w['batch']}/approve-all", json={}),
                                    c.post(f"/api/batches/{w['batch']}/approve-all", json={}))
    assert one.status_code == two.status_code == 200, (one.text, two.text)
    assert sorted((one.json()["approved"], two.json()["approved"]))[1] == 3
    first, second = await asyncio.gather(c.post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"}),
                                         c.post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"}))
    assert first.status_code in (200, 409) and second.status_code in (200, 409), (first.text, second.text)
    assert 200 in (first.status_code, second.status_code)
    jobs = []
    for res in (first, second):
        if res.status_code == 200:
            jobs += [j["job_id"] for j in res.json()["jobs"]]
    for job_id in jobs:
        # ("skipped": the other tab's job for the same listing and shop had it.)
        assert await publish_worker.run_publish_job(w["ctx"], job_id) in ("succeeded", "skipped"), job_id
    assert len(w["etsy"].listings) == 3  # however many jobs both tabs managed to queue
    async with w["sm"]() as s:
        assert len(list((await s.execute(select(ListingPublication))).scalars())) == 3
        failed = list((await s.execute(select(Job).where(Job.status == JobStatus.failed))).scalars())
    assert failed == []
    await w["engine"].dispose()
    _drop_scratch_postgres(url)


async def test_two_jobs_for_one_listing_and_shop_make_one_draft_and_neither_fails(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Two tabs in two API processes can both queue a job; the second finds the first's draft."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [job_id] = await _send_drafts(w)
    async with w["sm"]() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        twin = Job(tenant_id=job.tenant_id, connection_id=job.connection_id, type=job.type, payload=dict(job.payload),
                   batch_id=job.batch_id, status=JobStatus.queued)
        s.add(twin)
        await s.commit()
        twin_id = str(twin.id)
    await _run_drafts(w, [job_id])
    assert await publish_worker.run_publish_job(w["ctx"], twin_id) == "succeeded"
    assert len(w["etsy"].listings) == 1
    await w["engine"].dispose()


# --- 8. a batch deleted while its drafts are in progress ---------------------------------------------------


async def test_a_batch_deleted_with_drafts_queued_cancels_them_and_makes_nothing(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    jobs = await _send_drafts(w)
    assert (await w["client"].delete(f"/api/batches/{w['batch']}")).status_code in (200, 204)
    for job_id in jobs:
        await publish_worker.run_publish_job(w["ctx"], job_id)
        assert (await _job(w, job_id)).status is JobStatus.cancelled
    assert not w["etsy"].listings
    await w["engine"].dispose()


async def test_a_batch_deleted_while_a_draft_is_being_made_ends_clearly(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [job_id] = await _send_drafts(w)
    deleted = {"done": False}
    client = w["client"]

    def rule(request: httpx.Request):  # noqa: ANN202
        if request.method == "POST" and request.url.path.endswith("/images") and not deleted["done"]:
            deleted["done"] = True
            w["delete_now"] = True
        return None

    _answering(w, rule)
    original = publish_worker.publish_content

    async def deleting(*args, **kwargs):  # noqa: ANN002, ANN003
        assert (await client.delete(f"/api/batches/{w['batch']}")).status_code in (200, 204)
        return await original(*args, **kwargs)

    monkeypatch.setattr(publish_worker, "publish_content", deleting)
    outcome = await publish_worker.run_publish_job(w["ctx"], job_id)
    job = await _job(w, job_id)
    assert outcome == "succeeded" and job.status is JobStatus.succeeded  # it was already under way, and finished
    # The draft on Etsy is the seller's to see: a record, and the batch can be restored or it can be deleted there.
    assert len(w["etsy"].listings) == 1
    async with w["sm"]() as s:
        assert (await s.execute(select(ListingPublication))).scalar_one().etsy_listing_id in w["etsy"].listings
    res = await client.post(f"/api/batches/{w['batch']}/restore")
    assert res.status_code == 200, res.text
    await w["engine"].dispose()


# --- 9. a shop-connection callback refused, each reason ----------------------------------------------------


async def _refused_once(auth, reason: str) -> str:  # noqa: ANN001
    """Refuse the callback for ``reason``; returns the status the seller is sent back with."""
    async def started(client) -> str:  # noqa: ANN001
        start = await client.get("/api/auth/etsy/start")
        return parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    if reason == "denied":
        state = await started(auth)
        res = await auth.get(f"/api/auth/etsy/callback?state={state}&error=access_denied")
    elif reason == "etsy_error":
        state = await started(auth)
        res = await auth.get(f"/api/auth/etsy/callback?state={state}&error=invalid_scope&error_description=bad")
    elif reason == "expired":
        res = await auth.get("/api/auth/etsy/callback?state=never-issued&code=c")
    elif reason == "reused":
        state = await started(auth)
        await auth.get(f"/api/auth/etsy/callback?state={state}&code=c")
        state2 = state
        res = await auth.get(f"/api/auth/etsy/callback?state={state2}&code=c")
    elif reason == "no_binding":
        state = await started(auth)
        auth.cookies.delete(BINDING_COOKIE)
        res = await auth.get(f"/api/auth/etsy/callback?state={state}&code=c")
    elif reason == "state_mismatch":
        state = await started(auth)
        auth.cookies.set(BINDING_COOKIE, "someone-elses")
        res = await auth.get(f"/api/auth/etsy/callback?state={state}&code=c")
    elif reason == "other_account":
        state = await started(auth)
        other = await make_tenant(auth.sm, f"other-{uuid.uuid4().hex[:6]}@example.com")
        authenticate(auth, await open_session(auth.redis, other))
        res = await auth.get(f"/api/auth/etsy/callback?state={state}&code=c")
    else:
        raise AssertionError(reason)
    query = parse_qs(urlparse(res.headers["location"]).query)
    return query["status"][0]


@pytest.mark.parametrize("reason", ["denied", "etsy_error", "expired", "reused", "no_binding", "state_mismatch", "other_account"])
async def test_each_refused_callback_alerts_once_and_the_seller_sees_its_message(auth_client, reason: str) -> None:  # noqa: F811
    from pathlib import Path

    status = await _refused_once(auth_client, reason)
    assert status == reason
    banners = Path(__file__).resolve().parents[2] / "frontend/lib/connectStatus.ts"
    if banners.exists():
        assert status in banners.read_text(encoding="utf-8"), f"the Shops page has no words for {status}"
    inbox = Inbox()
    now = datetime.now(timezone.utc)
    await tick(auth_client.sm, auth_client.redis, now=now, send=inbox)
    refused = [m for m in inbox.messages if "shop connection refused" in m]
    assert len(refused) == 1, inbox.messages
    assert reason.split("_")[0] in refused[0] or "invalid_scope" in refused[0]
    await tick(auth_client.sm, auth_client.redis, now=now + timedelta(minutes=1), send=inbox)
    assert len([m for m in inbox.messages if "shop connection refused" in m]) == 1
    assert "the-code" not in " ".join(inbox.messages)


async def test_the_same_reason_many_times_is_one_alert(auth_client) -> None:  # noqa: F811
    for _ in range(5):
        assert await _refused_once(auth_client, "no_binding") == "no_binding"
    inbox = Inbox()
    await tick(auth_client.sm, auth_client.redis, now=datetime.now(timezone.utc), send=inbox)
    refused = [m for m in inbox.messages if "shop connection refused" in m]
    assert len(refused) == 1 and "5 times" in refused[0]


async def test_a_refused_callback_never_shows_the_other_accounts_address_or_a_token(auth_client) -> None:  # noqa: F811
    await _refused_once(auth_client, "other_account")
    inbox = Inbox()
    await tick(auth_client.sm, auth_client.redis, now=datetime.now(timezone.utc), send=inbox)
    text = " ".join(inbox.messages)
    assert "owner@example.com" not in text and "555." not in text and "the-state" not in text
    _ = (FakeAsyncRedis, BROWSER_HEADERS, JobType)
