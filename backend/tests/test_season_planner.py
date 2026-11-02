"""The seasonal planner (pipeline/season_calendar.py, pipeline/season_plan.py, api/season_planner.py).

The calendar's dates and windows, the spread across a window, daily limits, slots already taken and what does
not fit, all shown before anything is saved; a save only of what the preview showed; only approved listings,
and the approval checked again when it is due; another account's batch, group or shop 404.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, EtsyConnection, GeneratedContent, GroupPlan, Job, PlannedSlot
from app.pipeline import group_plan as G
from app.pipeline import season_calendar as C
from app.pipeline import season_plan as P
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_send import _group, _six

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


# --- the calendar ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key,year,on", [
    ("thanksgiving", 2026, date(2026, 11, 26)), ("thanksgiving", 2027, date(2027, 11, 25)),
    ("christmas", 2026, date(2026, 12, 25)), ("new_year", 2027, date(2027, 1, 1)), ("valentines", 2027, date(2027, 2, 14)),
    ("st_patricks", 2027, date(2027, 3, 17)), ("easter", 2027, date(2027, 3, 28)), ("easter", 2028, date(2028, 4, 16)), ("easter", 2029, date(2029, 4, 1)), ("easter", 2030, date(2030, 4, 21)), ("easter", 2031, date(2031, 4, 13)),
    ("mothers_day", 2027, date(2027, 5, 9)), ("fathers_day", 2027, date(2027, 6, 20)), ("july_4", 2027, date(2027, 7, 4)),
    ("back_to_school", 2027, date(2027, 8, 15)), ("halloween", 2027, date(2027, 10, 31)),
])
def test_the_dates_are_the_holidays(key: str, year: int, on: date) -> None:
    assert C._DATES[key](year) == on


def test_the_calendar_runs_from_today_forward_soonest_first_with_all_eleven() -> None:
    seasons = C.calendar(TODAY)
    assert [s.holiday.name for s in seasons[:3]] == ["Thanksgiving", "Christmas", "New Year"]
    assert {s.holiday.key for s in seasons} == set(C._DATES) and len(seasons) == 11
    assert all(s.on >= TODAY for s in seasons) and [s.on for s in seasons] == sorted(s.on for s in seasons)
    # It does not run out: there is always a next one.
    assert all(s.on >= date(2031, 1, 1) for s in C.calendar(date(2031, 1, 1)))


def test_each_window_is_a_real_window_before_its_holiday_and_is_our_advice() -> None:
    for s in C.calendar(TODAY):
        assert s.opens < s.closes < s.on, s.holiday.key
        assert s.holiday.why
    assert C.ABOUT.startswith("Our advice") and "Etsy publishes no" in C.ABOUT and C.REVIEWED <= TODAY


def test_a_window_already_open_starts_today_and_a_closed_one_says_so() -> None:
    thanks = C.season_of(C.BY_KEY["thanksgiving"], TODAY)
    assert thanks.opens < TODAY and thanks.first_left == TODAY and thanks.closes == date(2026, 11, 12)
    late = C.season_of(C.BY_KEY["thanksgiving"], date(2026, 11, 20))
    assert late.on == date(2026, 11, 26) and late.first_left is None
    ahead = C.season_of(C.BY_KEY["valentines"], TODAY)
    assert ahead.first_left == ahead.opens > TODAY


# --- the spread ------------------------------------------------------------------------------------------

SHOP_A, SHOP_B = uuid.uuid4(), uuid.uuid4()


def _settings(**over) -> G.Settings:
    return G.Settings(**{"start": TODAY, "per_shop_per_day": 5, "window_start": time(9), "window_end": time(17), "spacing_minutes": 30,
                         "stagger_minutes": 5, "zone": "UTC", **over})


def _listings(n: int, *skip_in) -> list[G.Listing]:
    return [G.Listing(uuid.uuid4(), f"Listing {i}", "g", {s: "x" for s in skip_in}) for i in range(n)]


def _shops(*daily) -> list[G.Shop]:
    ids = [SHOP_A, SHOP_B]
    return [G.Shop(ids[i], f"Shop {i}", "g", d) for i, d in enumerate(daily)]


def _window(days: int = 10) -> P.Window:
    return P.Window(date(2026, 11, 10), date(2026, 11, 10) + timedelta(days=days - 1))


def _spread(listings, shops, window=None, settings=None, taken=None, capacity=lambda d: 10_000) -> P.SeasonPlan:
    return P.spread(window or _window(), settings or _settings(), listings, shops, taken=taken or {}, capacity=capacity, now=NOW)


@pytest.mark.parametrize("count,days,want", [(1, 10, [0]), (3, 7, [0, 3, 6]), (2, 10, [0, 9]), (10, 10, list(range(10))), (6, 3, [0, 0, 1, 1, 2, 2])])
def test_listings_are_shared_evenly_across_the_days(count: int, days: int, want: list[int]) -> None:
    assert [P.even_day(i, count, days) for i in range(count)] == want


def test_the_spread_covers_the_whole_window_not_its_first_days() -> None:
    plan = _spread(_listings(10), _shops(None))
    days = sorted(s.local_day for s in plan.slots)
    assert days == _window().days and plan.did_not_fit == []
    assert plan.shops[0].total == 10 and plan.shops[0].moved == 0


def test_a_listing_never_goes_live_in_two_shops_in_the_same_minute() -> None:
    plan = _spread(_listings(20), _shops(None, None))
    assert len(plan.slots) == 40
    for content in {s.content_id for s in plan.slots}:
        both = [s.publish_at for s in plan.slots if s.content_id == content]
        assert len(set(both)) == 2


def test_the_daily_cap_holds_and_a_full_day_moves_a_listing_to_a_near_one() -> None:
    plan = _spread(_listings(25), _shops(2))
    per_day = Counter(s.local_day for s in plan.slots)
    assert max(per_day.values()) <= 2 and sum(per_day.values()) == 20
    assert len(plan.did_not_fit) == 5 and {m.why for m in plan.did_not_fit} == {P.NO_ROOM}


def test_what_is_taken_counts_against_the_limit_and_the_minutes_between() -> None:
    day = date(2026, 11, 10)
    taken = {SHOP_A: P.Taken({day: [9 * 60, 9 * 60 + 30]})}
    plan = _spread(_listings(10), _shops(3), taken=taken)
    row = plan.shops[0]
    assert row.taken == {day: 2} and row.new[day] == 1  # 3 a day, 2 already there
    minutes = [s.publish_at.hour * 60 + s.publish_at.minute for s in plan.slots if s.local_day == day]
    assert all(abs(m - t) >= 30 for m in minutes for t in taken[SHOP_A].minutes[day])


def test_a_day_full_of_others_moves_the_listing_and_counts_it() -> None:
    day = date(2026, 11, 10)
    plan = _spread(_listings(10), _shops(1), taken={SHOP_A: P.Taken({day: [600]})})
    assert plan.shops[0].moved >= 1 and day not in plan.shops[0].new
    assert len(plan.did_not_fit) == 1  # ten listings, nine days left at one a day


def test_the_budget_stops_a_day_and_says_it_was_the_budget() -> None:
    plan = _spread(_listings(5), _shops(None), capacity=lambda d: 0)
    assert plan.slots == [] and {m.why for m in plan.did_not_fit} == {P.NO_BUDGET}
    some = _spread(_listings(5), _shops(None), capacity=lambda d: 20 if d == date(2026, 11, 10) else 0)
    assert len(some.slots) == 1 and len(some.did_not_fit) == 4


def test_nothing_is_planned_in_the_past() -> None:
    plan = _spread(_listings(3), _shops(None), window=P.Window(TODAY, TODAY + timedelta(days=2)))
    assert all(s.publish_at > NOW for s in plan.slots)
    assert all(s.draft_at >= NOW for s in plan.slots)


def test_a_listing_skipped_in_a_shop_is_not_planned_there() -> None:
    plan = _spread(_listings(4, SHOP_B), _shops(None, None))
    assert {s.shop_id for s in plan.slots} == {SHOP_A}


def test_the_fingerprint_follows_the_slots() -> None:
    listings = _listings(4)
    one, same = _spread(listings, _shops(None)), _spread(listings, _shops(None))
    assert one.fingerprint() == same.fingerprint()
    assert one.fingerprint() != _spread(listings, _shops(None), window=_window(5)).fingerprint()


# --- the API --------------------------------------------------------------------------------------------


def _body(world, **over) -> dict:  # noqa: ANN001, F811
    return {"holiday": "christmas", "batch_ids": [str(world["batch"])], "connection_ids": [str(world["a1"]), str(world["a2"])],
            "first_day": "2026-11-10", "last_day": "2026-11-19", **over}


async def _preview(world, **over) -> dict:  # noqa: ANN001, F811
    res = await world["a"].post("/api/season-planner/preview", json=_body(world, **over))
    assert res.status_code == 200, res.text
    return res.json()


async def _slots(world) -> list[PlannedSlot]:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        return list((await s.execute(select(PlannedSlot))).scalars())


async def test_the_page_lists_the_calendar_and_the_accounts_own_choices(world) -> None:  # noqa: F811
    res = await world["a"].get("/api/season-planner")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["seasons"][0]["name"] == "Thanksgiving" and out["about"].startswith("Our advice")
    assert {s["name"] for s in out["shops"]} == {"Frost Tees", "Frost Mugs"}
    assert [b["id"] for b in out["batches"]] == [str(world["batch"])]
    assert "Bob" not in res.text


async def test_a_preview_shows_every_shops_days_and_saves_nothing(world) -> None:  # noqa: F811
    await _six(world)
    out = await _preview(world)
    assert out["disabled"] is None and out["listings"] >= 6 and out["shops"] == 2
    assert out["total"] == out["listings"] * 2 and out["button"] == f"Schedule {out['total']} go-lives"
    assert [d for d in out["days"]][:1] == ["2026-11-10"] and len(out["days"]) == 10
    assert {r["name"] for r in out["rows"]} == {"Frost Tees", "Frost Mugs"}
    for row in out["rows"]:
        assert row["total"] == sum(d["new"] for d in row["days"]) == out["listings"]
    assert out["fingerprint"]
    assert await _slots(world) == []
    for word in ("request", "budget_remaining", "per_draft"):
        assert word not in str(out)  # never a request count for a seller


async def test_only_approved_listings_are_planned_and_the_rest_are_said(world) -> None:  # noqa: F811
    await _six(world)
    async with world["sm"]() as s:
        rows = list((await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == world["batch"]))).scalars())
        for row in rows[:2]:
            row.approved = False
        await s.commit()
        out_ids = {str(r.id) for r in rows[:2]}
    out = await _preview(world)
    assert out["listings"] == len(rows) - 2
    assert sum(1 for x in out["left_out"] if x["reason"] == "not approved yet") == 2
    res = await world["a"].post("/api/season-planner/save", json={**_body(world), "fingerprint": out["fingerprint"]})
    assert res.status_code == 200, res.text
    assert not ({str(s.content_id) for s in await _slots(world)} & out_ids)


async def test_nothing_approved_cannot_be_saved(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        for row in (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == world["batch"]))).scalars():
            row.approved = False
        await s.commit()
    out = await _preview(world)
    assert out["total"] == 0 and "only approved listings" in out["disabled"]


async def test_saving_writes_the_ordinary_slots_and_makes_no_draft_until_it_is_due(world) -> None:  # noqa: F811
    await _six(world)
    out = await _preview(world)
    res = await world["a"].post("/api/season-planner/save", json={**_body(world), "fingerprint": out["fingerprint"]})
    assert res.status_code == 200, res.text
    assert res.json()["scheduled"] == out["total"] and "Scheduled page" in res.json()["message"]
    slots = await _slots(world)
    assert len(slots) == out["total"] and {s.state for s in slots} == {"waiting"}
    async with world["sm"]() as s:
        plan = (await s.execute(select(GroupPlan))).scalars().one()
        assert plan.settings["mode"] == "season" and plan.settings["holiday"] == "christmas"
        assert (await s.execute(select(Job))).scalars().first() is None  # the window is days away: nothing queued for Etsy
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "schedule.season_planned"))).scalars().first() is not None
    # The Scheduled page shows them, as any other schedule.
    assert (await world["a"].get("/api/scheduled")).json()["items"][0]["kind"] == "planned"
    # Pressing again sends nothing twice.
    again = await _preview(world)
    assert again["total"] == 0 and "already" in again["disabled"]


async def test_a_plan_that_changed_since_the_preview_is_not_saved(world) -> None:  # noqa: F811
    await _six(world)
    out = await _preview(world)
    async with world["sm"]() as s:
        row = (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == world["batch"]))).scalars().first()
        row.approved = False
        await s.commit()
    res = await world["a"].post("/api/season-planner/save", json={**_body(world), "fingerprint": out["fingerprint"]})
    assert res.status_code == 409 and "changed since this preview" in res.text
    assert await _slots(world) == []


async def test_daily_caps_and_slots_taken_are_shown_before_saving(world) -> None:  # noqa: F811
    await _six(world)
    async with world["sm"]() as s:
        shop = await s.get(EtsyConnection, world["a1"])
        shop.daily_limit = 1
        await s.commit()
    out = await _preview(world, first_day="2026-11-10", last_day="2026-11-11")
    row = next(r for r in out["rows"] if r["name"] == "Frost Tees")
    assert row["limit"] == 1 and row["cap"] == 1 and row["total"] == 2
    assert out["did_not_fit"] and all(m["shop_name"] == "Frost Tees" for m in out["did_not_fit"])
    assert any("will not fit" in c for c in out["conflicts"])
    # What this plan saves is what the next plan sees as taken.
    first = await world["a"].post("/api/season-planner/save", json={**_body(world, first_day="2026-11-10", last_day="2026-11-11"), "fingerprint": out["fingerprint"]})
    assert first.status_code == 200
    next_one = await _preview(world, first_day="2026-11-10", last_day="2026-11-11")
    frost = next(r for r in next_one["rows"] if r["name"] == "Frost Tees")
    assert frost["taken"] == 2 and all(d["new"] + d["taken"] <= 1 for d in frost["days"])
    assert any("Frost Tees already has 2 listings going live" in c for c in next_one["conflicts"])


async def test_a_closed_window_asks_for_dates_and_bad_dates_are_said(world) -> None:  # noqa: F811
    await _six(world)
    closed = await world["a"].post("/api/season-planner/preview", json={"holiday": "thanksgiving", "batch_ids": [str(world["batch"])], "connection_ids": [str(world["a1"])]})
    assert closed.json()["first_day"] is not None  # today is inside it, in these tests
    out = await _preview(world, first_day="2026-12-20", last_day="2026-12-10")
    assert "last day" in out["disabled"] and out["fingerprint"] is None
    past = await _preview(world, first_day="2026-01-01", last_day="2026-11-20")
    assert past["first_day"] >= TODAY.isoformat()  # never before today
    after = await _preview(world, holiday="thanksgiving", first_day="2026-11-27", last_day="2026-11-30")
    assert any("after Thanksgiving" in c for c in after["conflicts"])
    assert (await world["a"].post("/api/season-planner/preview", json=_body(world, holiday="arbor_day"))).status_code == 422


async def test_no_shop_chosen_says_so(world) -> None:  # noqa: F811
    out = await _preview(world, connection_ids=[])
    assert out["disabled"].startswith("Choose where") and out["total"] == 0


async def test_a_group_gives_its_shops_each_shop_once(world) -> None:  # noqa: F811
    await _six(world)
    group = await _group(world, "All", world["a1"], world["a2"])
    out = await _preview(world, connection_ids=[str(world["a1"])], group_ids=[group])
    assert out["shops"] == 2 and out["total"] == out["listings"] * 2


async def test_another_accounts_batch_group_and_shop_are_404(world) -> None:  # noqa: F811
    await _six(world)
    bob = world["b"]
    assert (await bob.post("/api/season-planner/preview", json=_body(world, connection_ids=[]))).status_code == 404
    assert (await bob.post("/api/season-planner/save", json={**_body(world, connection_ids=[]), "fingerprint": "x"})).status_code == 404
    group = await _group(world, "Theirs", world["a1"])
    assert (await world["b"].get("/api/season-planner")).text.count("Frost") == 0
    res = await world["a"].post("/api/season-planner/preview", json=_body(world, connection_ids=[str(world["b1"])]))
    assert res.status_code == 404
    mine = await bob.post("/api/season-planner/preview", json={"holiday": "christmas", "batch_ids": [str(uuid.uuid4())], "group_ids": [group]})
    assert mine.status_code == 404
    assert await _slots(world) == []


async def test_an_unapproved_listing_never_goes_on_when_its_slot_comes_due(world) -> None:  # noqa: F811
    from app.workers.plans import release_due

    await _six(world)
    out = await _preview(world)
    await world["a"].post("/api/season-planner/save", json={**_body(world), "fingerprint": out["fingerprint"]})
    async with world["sm"]() as s:
        for row in (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == world["batch"]))).scalars():
            row.approved = False
        for slot in (await s.execute(select(PlannedSlot))).scalars():
            slot.draft_at = NOW - timedelta(minutes=1)
        await s.commit()
        enqueued: list = []

        async def enqueue(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
            enqueued.append(args)

        async def submit(tenant_id, job_id) -> None:  # noqa: ANN001
            enqueued.append(job_id)

        await release_due(s, enqueue, submit=submit)
    assert enqueued == []
    assert all(slot.state != "drafting" for slot in await _slots(world))
