"""AI cost over time, per seller: Istanbul buckets, the UTC day beside them, the
previous equal period, the seller filter, fixed colours. Admin only."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import update

from app.api import ai_series
from app.core import ai_prices
from sqlalchemy import func, select

from app.db.models import AiCall, Tenant
from app.workers.retention import purge_expired_rows
from tests.test_admin import world  # noqa: F401  (fixture)

MODEL = "claude-sonnet-5"
TABLE = ai_prices.merged(None)
#: Sunday 4 October 2026, 16:30 in Istanbul.
NOW = datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _call(tenant, at: datetime, *, tokens=(1000, 100), ok=True, listings=0, model=MODEL) -> AiCall:
    row = AiCall(at=at, day=at.date(), tenant_id=tenant, purpose="content", model=model, ok=ok, error=None if ok else "refusal",
                 input_tokens=tokens[0], output_tokens=tokens[1], cache_write_tokens=0, cache_write_1h_tokens=0,
                 cache_read_tokens=0, listings=listings)
    row.cost_usd = ai_prices.cost_of(row, TABLE)
    return row


#: 1,000 in and 100 out on Sonnet 5 ($2 / $10 per million): $0.003.
UNIT = Decimal("0.003")


async def _seed(world) -> tuple[uuid.UUID, uuid.UUID]:  # noqa: F811
    bob, admin = world["bob"].tenant_id, world["admin"].tenant_id
    async with world["sm"]() as s:
        # Colours go by the age of the account: say which is older.
        await s.execute(update(Tenant).where(Tenant.id == admin).values(created_at=_utc("2026-01-01T00:00")))
        await s.execute(update(Tenant).where(Tenant.id == bob).values(created_at=_utc("2026-01-02T00:00")))
        s.add_all([
            # 00:30 on the 4th in Istanbul is still the 3rd in UTC.
            _call(bob, _utc("2026-10-03T21:30"), listings=1),
            _call(bob, _utc("2026-10-04T10:00"), ok=False),
            # 23:30 on the 3rd in Istanbul, the 3rd in UTC too.
            _call(admin, _utc("2026-10-03T20:30"), listings=1),
            _call(None, _utc("2026-10-04T13:10")),  # an evaluation run of ours
            # The day before, for "previous": 14:00 Istanbul on the 3rd, before the cut at 16:30.
            _call(bob, _utc("2026-10-03T11:00"), listings=1),
            _call(bob, _utc("2026-10-03T11:05"), listings=1),
            # 16:45 Istanbul on the 3rd: after the cut, before this period's first hour.
            _call(bob, _utc("2026-10-03T13:45")),
            # The first record there is: early enough that the earlier periods are covered.
            _call(admin, _utc("2026-07-01T09:00")),
        ])
        await s.commit()
    return bob, admin


def _bucket(out: ai_series.AiSeriesOut, title: str) -> int:
    return next(i for i, b in enumerate(out.buckets) if b.title == title)


async def test_days_are_istanbul_days_with_the_utc_day_beside_them(world) -> None:  # noqa: F811
    bob, admin = await _seed(world)
    async with world["sm"]() as s:
        out = await ai_series.build(s, "daily", now=NOW)

    assert out.time_zone == "Europe/Istanbul" and len(out.buckets) == 31
    assert out.start.isoformat() == "2026-09-04T00:00:00+03:00" and out.end.isoformat() == "2026-10-05T00:00:00+03:00"
    today, yesterday = _bucket(out, "Sun 4 Oct 2026"), _bucket(out, "Sat 3 Oct 2026")
    assert out.buckets[today].partial and not out.buckets[yesterday].partial
    by = {x.email: x for x in out.sellers}
    seller = by["bob@example.com"]
    # Istanbul's 4th holds the 00:30 call; the UTC 4th does not.
    assert (seller.cells[today].calls, seller.cells[today].failed, seller.cells[today].listings) == (2, 1, 1)
    assert seller.cells[yesterday].calls == 3
    assert out.buckets[today].total.calls == 3  # bob's two and our own run
    assert (out.buckets[today].utc_day, out.buckets[today].utc.calls) == ("2026-10-04", 2)
    assert (out.buckets[yesterday].utc_day, out.buckets[yesterday].utc.calls) == ("2026-10-03", 5)
    assert Decimal(out.buckets[yesterday].utc.cost_usd) == UNIT * 5

    # Rows and columns add up to the same total.
    assert Decimal(out.total.cost_usd) == sum(Decimal(x.total.cost_usd) for x in out.sellers) == UNIT * 7
    assert Decimal(out.total.cost_usd) == sum(Decimal(b.total.cost_usd) for b in out.buckets)
    for i, b in enumerate(out.buckets):
        assert b.total.calls == sum(x.cells[i].calls for x in out.sellers)
    assert abs(sum(Decimal(x.share) for x in out.sellers) - 1) < Decimal("0.001")
    assert seller.share == "0.7143" and seller.total.cost_per_listing_usd == f"{UNIT * 5 / 3:.6f}"
    assert out.total.listings == 4 and out.total.failed == 1

    # A colour belongs to the account, oldest first; our own runs have none.
    assert (by["admin@example.com"].slot, seller.slot, by[None].slot, by[None].id) == (0, 1, None, "none")
    assert [o.id for o in out.options] == [str(admin), str(bob), "none"]


async def test_hours_and_the_previous_equal_period(world) -> None:  # noqa: F811
    await _seed(world)
    async with world["sm"]() as s:
        out = await ai_series.build(s, "24h", now=NOW)
        two_days = await ai_series.build(s, "48h", now=NOW)

    assert len(out.buckets) == 24 and len(two_days.buckets) == 48
    assert out.buckets[0].title == "Sat 3 Oct, 17:00 to 18:00" and out.buckets[-1].title == "Sun 4 Oct, 16:00 to 17:00"
    assert out.buckets[-1].partial and out.buckets[-1].utc_day is None
    midnight = _bucket(out, "Sun 4 Oct, 00:00 to 01:00")
    assert out.buckets[midnight].label == "4 Oct" and out.buckets[midnight].total.calls == 1
    assert out.buckets[_bucket(out, "Sat 3 Oct, 23:00 to 00:00")].total.calls == 1
    assert out.total.calls == 4 and Decimal(out.total.cost_usd) == UNIT * 4

    # The 24 hours before, up to 16:30 yesterday: two calls. The 16:45 call is in neither.
    assert out.previous_start.isoformat() == "2026-10-02T17:00:00+03:00"
    assert out.previous_end.isoformat() == "2026-10-03T16:30:00+03:00"
    assert out.previous_covered and out.records_from == "2026-07-01"
    assert (out.previous.calls, out.previous.listings, Decimal(out.previous.cost_usd)) == (2, 2, UNIT * 2)
    assert out.change.cost == "100.0" and out.change.listings == "0.0"
    # $0.012 over 2 listings now, $0.006 over 2 before.
    assert out.change.cost_per_listing == "100.0"
    assert two_days.total.calls == 7


async def test_weeks_start_on_monday_and_months_on_the_first(world) -> None:  # noqa: F811
    await _seed(world)
    async with world["sm"]() as s:
        weekly = await ai_series.build(s, "weekly", now=NOW)
        monthly = await ai_series.build(s, "monthly", now=NOW)

    assert len(weekly.buckets) == 12 and weekly.buckets[-1].title == "28 Sep to 4 Oct 2026"
    assert weekly.buckets[0].start.isoformat() == "2026-07-13T00:00:00+03:00"
    assert weekly.buckets[-1].total.calls == 7
    # Twelve weeks earlier starts in April: before the first record, so nothing to compare with.
    assert not weekly.previous_covered and weekly.change.cost is None and weekly.change.listings is None

    assert [b.title for b in monthly.buckets][0] == "November 2025" and monthly.buckets[-1].title == "October 2026"
    assert monthly.buckets[2].label == "Jan 2026"
    assert monthly.buckets[_bucket(monthly, "July 2026")].total.calls == 1 and monthly.buckets[-1].total.calls == 7
    assert monthly.previous_start.isoformat() == "2024-11-01T00:00:00+03:00"
    assert monthly.previous_end.isoformat() == "2025-10-04T16:30:00+03:00"
    assert not monthly.previous_covered and monthly.change.cost is None


async def test_calls_are_kept_long_enough_to_compare_twelve_months_with_the_twelve_before(world) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    async with world["sm"]() as s:
        s.add_all([
            _call(bob, _utc("2026-10-02T09:00"), listings=1),  # the last twelve months
            _call(bob, _utc("2026-03-10T09:00"), listings=1),
            _call(bob, _utc("2025-06-15T09:00"), listings=1),  # the twelve before them
            _call(bob, _utc("2024-11-01T09:00")),  # their first day
            _call(bob, _utc("2024-10-15T09:00")),  # before both, within 25 months: kept, counted in neither
            _call(bob, _utc("2024-08-01T09:00")),  # older than 25 months: deleted
        ])
        await s.commit()
        await purge_expired_rows(s, now=NOW)
        await s.commit()
        assert (await s.execute(select(func.count()).select_from(AiCall))).scalar() == 5
        assert (await s.execute(select(func.min(AiCall.day)))).scalar().isoformat() == "2024-10-15"
        out = await ai_series.build(s, "monthly", now=NOW)

    # Whatever is still kept reaches back past the start of the previous twelve months.
    assert (NOW - out.previous_start).days < AiCall.RETENTION_DAYS
    assert out.previous_covered and out.records_from == "2024-10-15"
    assert (out.total.calls, out.total.listings, Decimal(out.total.cost_usd)) == (2, 2, UNIT * 2)
    assert (out.previous.calls, out.previous.listings, Decimal(out.previous.cost_usd)) == (2, 1, UNIT * 2)
    # The same cost over twice the listings: half the cost per listing.
    assert (out.change.cost, out.change.listings, out.change.cost_per_listing) == ("0.0", "100.0", "-50.0")


async def test_one_seller_keeps_their_colour_and_their_share_of_everyone(world) -> None:  # noqa: F811
    bob, admin = await _seed(world)
    async with world["sm"]() as s:
        everyone = await ai_series.build(s, "daily", now=NOW)
        one = await ai_series.build(s, "daily", str(bob), now=NOW)
        ours = await ai_series.build(s, "daily", "none", now=NOW)

    assert [x.email for x in one.sellers] == ["bob@example.com"]
    kept = next(x for x in everyone.sellers if x.id == str(bob))
    assert (one.sellers[0].slot, one.sellers[0].share) == (kept.slot, kept.share) == (1, "0.7143")
    assert one.total.cost_usd == kept.total.cost_usd and one.all_sellers.cost_usd == everyone.total.cost_usd
    assert [b.total.calls for b in one.buckets] == [c.calls for c in kept.cells]
    today = _bucket(one, "Sun 4 Oct 2026")
    assert one.buckets[today].utc.calls == 1  # the UTC day is filtered too
    assert [o.id for o in one.options] == [o.id for o in everyone.options]  # the filter can still pick anyone

    assert [x.id for x in ours.sellers] == ["none"] and ours.total.calls == 1


async def test_a_ninth_seller_gets_no_colour(world) -> None:  # noqa: F811
    await _seed(world)
    async with world["sm"]() as s:
        base = datetime(2030, 1, 1, tzinfo=timezone.utc)
        late = [Tenant(email=f"late{i}@example.com", password_hash="x", created_at=base + timedelta(days=i)) for i in range(7)]
        s.add_all(late)
        await s.flush()
        s.add_all([_call(t.id, _utc("2026-10-04T09:00")) for t in late])
        await s.commit()
        out = await ai_series.build(s, "daily", now=NOW)
    slots = {x.email: x.slot for x in out.sellers}
    assert sorted(v for v in slots.values() if v is not None) == list(range(ai_series.SLOTS))
    assert slots["late5@example.com"] == 7 and slots["late6@example.com"] is None


async def test_the_series_is_admin_only_and_carries_nothing_of_the_work(world) -> None:  # noqa: F811
    bob, _ = await _seed(world)
    for path in ("/api/admin/ai-cost/series", f"/api/admin/ai-cost/series?period=24h&seller={bob}"):
        assert (await world["b"].get(path)).status_code == 404
        assert (await world["anon"].get(path)).status_code in (401, 404)

    seen = await world["a"].get("/api/admin/ai-cost/series")
    assert seen.status_code == 200 and seen.json()["period"] == "daily" and len(seen.json()["buckets"]) == 31
    for period, count in (("24h", 24), ("48h", 48), ("weekly", 12), ("monthly", 12)):
        body = (await world["a"].get(f"/api/admin/ai-cost/series?period={period}&seller={bob}")).json()
        assert len(body["buckets"]) == count and body["seller"] == str(bob)
        assert all(len(x["cells"]) == count for x in body["sellers"])
    assert (await world["a"].get("/api/admin/ai-cost/series?period=yearly")).status_code == 422
    assert (await world["a"].get("/api/admin/ai-cost/series?seller=bob")).status_code == 422
    # Counts and cost only: nothing of the seller's work is in it.
    assert str(world["bob"].batch_id) not in seen.text
    assert set(seen.json()["sellers"][0]) == {"id", "email", "slot", "total", "share", "cells"}
