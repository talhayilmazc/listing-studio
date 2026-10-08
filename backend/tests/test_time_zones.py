"""Schedules in the seller's own time zone, and scheduled go-lives before other work.

A wall-clock time is converted to UTC once, on save, with the zone's rules for
that date; the stored instant is what runs. The budget a day's scheduled
go-lives need is held back from the seller's other jobs, so they are not pushed
to the 00:00 UTC reset (7 PM in US Central summer time).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fakeredis import FakeAsyncRedis

from app.core.timezones import TimeRefused, to_utc, valid_zone, wall_clock, zone_abbreviation
from app.db.models import ListingPublication, Tenant
from app.etsy.rate_limiter import PAUSE_TENANT, DailyQuota
from app.workers import gate
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)
from tests.test_schedules import _pub

UTC = timezone.utc


def test_a_wall_clock_time_is_converted_once_with_that_dates_rules() -> None:
    # 5 PM in Chicago: CDT (UTC-5) in September, CST (UTC-6) in December. A time
    # chosen now for after the November change still goes out at 5 PM there.
    assert to_utc("2026-09-28T17:00", "America/Chicago") == datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
    assert to_utc("2026-12-07T17:00", "America/Chicago") == datetime(2026, 12, 7, 23, 0, tzinfo=UTC)
    assert to_utc("2026-09-28T17:00", "Europe/Istanbul") == datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    assert zone_abbreviation(datetime(2026, 9, 28, 22, 0, tzinfo=UTC), "America/Chicago") == "CDT"
    assert zone_abbreviation(datetime(2026, 12, 7, 23, 0, tzinfo=UTC), "America/New_York") == "EST"
    assert wall_clock(datetime(2026, 12, 7, 23, 0, tzinfo=UTC), "America/Chicago") == "2026-12-07T17:00"


def test_daylight_saving_edges() -> None:
    # Spring forward: 2:30 AM on 8 March 2026 never happens in Chicago.
    with pytest.raises(TimeRefused, match="doesn't exist"):
        to_utc("2026-03-08T02:30", "America/Chicago")
    # Fall back: 1:30 AM on 1 November happens twice; the first (CDT) is used.
    first = to_utc("2026-11-01T01:30", "America/Chicago")
    assert first == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert zone_abbreviation(first, "America/Chicago") == "CDT"
    for bad in ("", "tomorrow", "2026-09-28T17:00+02:00"):
        with pytest.raises(TimeRefused):
            to_utc(bad, "America/Chicago")
    assert valid_zone("America/Chicago") and not valid_zone("Mars/Olympus") and not valid_zone(None)


async def _set_zone(ctx, zone: str | None) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        (await s.get(Tenant, ctx["tenant_id"])).time_zone = zone
        await s.commit()


async def test_the_account_zone_is_detected_once_and_editable(ctx) -> None:  # noqa: F811
    c = ctx["client"]
    assert (await c.get("/api/account/me")).json()["time_zone"] is None
    r = await c.put("/api/account/time-zone", json={"time_zone": "America/Chicago", "detected": True})
    assert r.json()["time_zone"] == "America/Chicago"
    # Detection never overrides a zone already set; Settings does.
    r = await c.put("/api/account/time-zone", json={"time_zone": "Europe/Berlin", "detected": True})
    assert r.json()["time_zone"] == "America/Chicago"
    r = await c.put("/api/account/time-zone", json={"time_zone": "America/Los_Angeles"})
    assert r.json()["time_zone"] == "America/Los_Angeles"
    assert (await c.put("/api/account/time-zone", json={"time_zone": "Nowhere/City"})).status_code == 422


async def test_a_schedule_is_entered_and_shown_in_the_account_zone(ctx) -> None:  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    day = (datetime.now(UTC) + timedelta(days=3)).date()
    item = {"content_id": str(content_id), "connection_id": str(shop), "local_time": f"{day}T17:00"}

    body = (await ctx["client"].post("/api/schedules", json={"items": [item]})).json()
    assert body["skipped"][0]["reason"] == "set your time zone in Settings first"

    await _set_zone(ctx, "America/Chicago")
    body = (await ctx["client"].post("/api/schedules", json={"items": [item]})).json()
    [row] = body["scheduled"]
    stored = (await _pub(ctx, content_id)).scheduled_for
    stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
    assert stored == to_utc(f"{day}T17:00", "America/Chicago")  # 5 PM there, stored once in UTC
    assert row["time_zone"] == "America/Chicago" and row["zone_abbreviation"] in ("CDT", "CST")


async def test_scheduled_go_lives_keep_their_budget(ctx) -> None:  # noqa: F811
    redis = FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=5000)
    async with ctx["sm"]() as s:
        tenant = await s.get(Tenant, ctx["tenant_id"])
        tenant.etsy_ceiling_override = 20
        await s.commit()
    # Three go-lives due before tonight's reset: 3 x 3 requests held back.
    for listing_id in (801, 802, 803):
        cid = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=listing_id)
        async with ctx["sm"]() as s:
            pub = (await s.execute(
                ListingPublication.__table__.select().where(ListingPublication.content_id == cid)
            )).first()
            row = await s.get(ListingPublication, pub.id)
            row.scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
            await s.commit()
    wctx = {"quota": quota, "sessionmaker": ctx["sm"]}
    assert await gate.scheduled_reserve(wctx, ctx["tenant_id"]) == 9

    async with ctx["sm"]() as s:
        tenant = await s.get(Tenant, ctx["tenant_id"])
    # A draft (15 requests until drafts are measured) fits 20 but not the 11 left beside the go-lives: it waits...
    verdict = await gate.check(wctx, tenant, "run_publish_job")
    assert verdict.action == "paused" and verdict.reason == PAUSE_TENANT
    # ...and the go-lives themselves still run.
    assert (await gate.check(wctx, tenant, "run_publish_live_job")).action == "run"
    assert (await gate.check({"quota": quota}, tenant, "run_publish_job")).action == "run"  # nothing held
