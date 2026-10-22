"""The Scheduled page (B8): editing go-lives one by one and in bulk.

* a single edit, and each bulk action (set time with minutes between, shift, spread, move to
  a day, publish now, remove), with the preview matching what is saved, and undo;
* the rules after every edit: minutes between listings in a shop, a shop's daily limit
  across groups, one design never live in two shops in the same minute; said in one line;
* times are the seller's wall clock, converted with that day's rules;
* pausing a group (or everything) holds its go-lives without cancelling them;
* a group's settings reflow its shops' listings, previewed first;
* another account gets 404.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import EtsyConnection, GroupPlan, PlannedSlot, Tenant
from app.pipeline import reschedule as R
from app.workers import plans
from tests.test_multi_shop import world  # noqa: F401  (fixture)

ZONE = "America/Chicago"
DAY = (datetime.now(ZoneInfo(ZONE)) + timedelta(days=3)).date()


def _at(clock: str, day: date = DAY) -> datetime:
    return R.instant(day, time.fromisoformat(clock), ZONE)


def _local(day: date, clock: str) -> str:
    return f"{day.isoformat()}T{clock}"


async def _slots(world, spec: list[tuple[int, str, str]]) -> list[str]:  # noqa: F811
    """Planned go-lives: (content index, shop key, wall clock on DAY) -> their keys."""
    async with world["sm"]() as s:
        (await s.get(Tenant, world["alice"])).time_zone = ZONE
        plan = GroupPlan(tenant_id=world["alice"], batch_id=world["batch"], settings={"mode": "spread"})
        s.add(plan)
        await s.flush()
        keys = []
        for index, shop, clock in spec:
            at = _at(clock)
            slot = PlannedSlot(tenant_id=world["alice"], plan_id=plan.id, content_id=world["contents"][index],
                               connection_id=world[shop], draft_at=at - timedelta(hours=2), publish_at=at)
            s.add(slot)
            await s.flush()
            keys.append(f"slot:{slot.id}")
        await s.commit()
        return keys


async def _edit(world, op: str, keys: list[str], **params) -> tuple[dict, dict]:  # noqa: F811
    body = {"op": op, "keys": keys, "params": params}
    preview = await world["a"].post("/api/scheduled/preview", json=body)
    assert preview.status_code == 200, preview.text
    saved = await world["a"].post("/api/scheduled/apply", json=body)
    assert saved.status_code == 200, saved.text
    return preview.json(), saved.json()


async def _times(world) -> dict[str, str]:  # noqa: F811
    data = (await world["a"].get("/api/scheduled")).json()
    return {i["key"]: i["local_time"] for i in data["items"]}


async def test_the_page_lists_go_lives_in_the_sellers_time(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00"), (1, "a2", "10:30")])
    data = (await world["a"].get("/api/scheduled")).json()
    assert data["time_zone"] == ZONE
    items = {i["key"]: i for i in data["items"]}
    assert items[keys[0]]["local_time"] == "09:00" and items[keys[0]]["local_day"] == DAY.isoformat()
    assert items[keys[0]]["status"] == "planned" and items[keys[0]]["movable"] is True
    assert items[keys[1]]["shop_name"] == "Frost Mugs" and items[keys[1]]["local"].endswith(("CDT", "CST"))


async def test_one_row_moves_and_the_preview_is_what_is_saved(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00")])
    preview, saved = await _edit(world, "times", keys, times={keys[0]: _local(DAY, "14:15")})
    assert preview["rows"][0]["new_local"].find("2:15 PM") >= 0 and preview["sentence"] == "1 listing moves."
    assert saved["changed"] == 1 and (await _times(world))[keys[0]] == "14:15"
    async with world["sm"]() as s:
        slot = await s.get(PlannedSlot, uuid.UUID(keys[0][5:]))
        assert slot.draft_at.replace(tzinfo=timezone.utc) == _at("14:15") - timedelta(hours=2)  # its draft follows


async def test_set_time_shift_spread_and_move_to_day(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00"), (1, "a2", "09:30")])
    await _edit(world, "set_time", keys, local_time=_local(DAY, "12:00"), minutes_between=20)
    assert [(await _times(world))[k] for k in keys] == ["12:00", "12:20"]
    await _edit(world, "shift", keys, minutes=-60)
    assert [(await _times(world))[k] for k in keys] == ["11:00", "11:20"]
    await _edit(world, "spread", keys, **{"from": _local(DAY, "08:00"), "to": _local(DAY, "16:00")})
    assert [(await _times(world))[k] for k in keys] == ["08:00", "16:00"]
    other = DAY + timedelta(days=2)
    await _edit(world, "move_day", keys, date=other.isoformat())
    data = {i["key"]: i for i in (await world["a"].get("/api/scheduled")).json()["items"]}
    assert [(data[k]["local_day"], data[k]["local_time"]) for k in keys] == [(other.isoformat(), "08:00"), (other.isoformat(), "16:00")]


async def test_remove_and_undo_put_it_back(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00")])
    preview, saved = await _edit(world, "remove", keys)
    assert preview["rows"][0]["removed"] is True and "leaves the schedule" in preview["sentence"]
    assert keys[0] not in await _times(world)
    res = await world["a"].post("/api/scheduled/restore", json={"undo": saved["undo"]})
    assert res.status_code == 200 and res.json()["changed"] == 1
    assert (await _times(world))[keys[0]] == "09:00"
    # A move is undone the same way.
    _, moved = await _edit(world, "shift", keys, minutes=90)
    await world["a"].post("/api/scheduled/restore", json={"undo": moved["undo"]})
    assert (await _times(world))[keys[0]] == "09:00"


async def test_publish_now_makes_the_draft_at_once(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00")])
    preview, saved = await _edit(world, "publish_now", keys)
    assert "go live in the next few minutes" in preview["sentence"] or "goes live" in preview["sentence"]
    assert [c for c in world["queue"].calls if c[0] == "run_publish_job"]
    async with world["sm"]() as s:
        assert (await s.get(PlannedSlot, uuid.UUID(keys[0][5:]))).state == "drafting"


async def test_minutes_between_and_the_same_minute_are_kept(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00"), (1, "a1", "11:00")])
    a = (await world["a"].post("/api/shop-groups", json={"name": "A", "connection_ids": [str(world["a1"])]})).json()
    group = a["groups"][0]["id"]
    await world["a"].put(f"/api/scheduled/groups/{group}", json={"settings": {
        "per_shop_per_day": 10, "window_start": "08:00", "window_end": "18:00", "spacing_minutes": 30, "stagger_minutes": 5}})
    preview, _ = await _edit(world, "times", [keys[1]], times={keys[1]: _local(DAY, "09:10")})
    assert (await _times(world))[keys[1]] == "09:30"  # 30 minutes after the other listing in Frost Tees
    assert preview["moved_line"] == "Keeping the rules moves 1 later to keep 30 minutes between listings in Frost Tees."
    # One design in two shops: never the same minute.
    more = await _slots(world, [(0, "a2", "15:00")])
    await _edit(world, "times", more, times={more[0]: _local(DAY, "09:00")})
    assert (await _times(world))[more[0]] == "09:01"


async def test_a_shops_daily_limit_counts_across_schedules(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        (await s.get(EtsyConnection, world["a1"])).daily_limit = 1
        await s.commit()
    first = await _slots(world, [(0, "a1", "09:00")])  # one plan
    second = await _slots(world, [(1, "a1", "09:00")])  # another plan, other day after the move
    await _edit(world, "move_day", second, date=DAY.isoformat())
    data = {i["key"]: i for i in (await world["a"].get("/api/scheduled")).json()["items"]}
    assert data[first[0]]["local_day"] == DAY.isoformat()
    assert data[second[0]]["local_day"] == (DAY + timedelta(days=1)).isoformat()  # Frost Tees takes 1 a day


def test_wall_clock_times_use_that_days_rules() -> None:
    summer = R.parse_local("2026-07-01T09:00", ZONE)
    winter = R.parse_local("2026-12-01T09:00", ZONE)
    assert (summer.hour, winter.hour) == (14, 15)  # CDT then CST
    skipped = R.parse_local("2027-03-14T02:30", ZONE)  # the clocks jump from 2:00 to 3:00
    assert skipped.astimezone(ZoneInfo(ZONE)).hour == 3


async def test_pausing_a_group_holds_its_go_lives_and_resuming_lets_them_go(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00")])
    g = (await world["a"].post("/api/shop-groups", json={"name": "A", "connection_ids": [str(world["a1"])]})).json()["groups"][0]["id"]
    res = await world["a"].put(f"/api/scheduled/groups/{g}", json={"paused": True})
    assert res.json()["groups"][0]["paused"] is True and res.json()["items"][0]["paused"] is True
    later = datetime.now(timezone.utc) + timedelta(days=10)
    async with world["sm"]() as s:
        assert await plans.release_due(s, lambda *a: _noop(), now=later) == 0  # held, not cancelled
        assert (await s.get(PlannedSlot, uuid.UUID(keys[0][5:]))).state == "waiting"
    await world["a"].put(f"/api/scheduled/groups/{g}", json={"paused": False})
    await world["a"].put("/api/scheduled/paused", json={"paused": True})
    async with world["sm"]() as s:
        assert await plans.release_due(s, lambda *a: _noop(), now=later) == 0  # everything paused
    await world["a"].put("/api/scheduled/paused", json={"paused": False})
    async with world["sm"]() as s:
        assert await plans.release_due(s, lambda *a: _noop(), now=later) == 1


async def _noop() -> None:
    return None


async def test_a_groups_settings_reflow_its_listings_after_a_preview(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00"), (1, "a1", "09:05")])
    g = (await world["a"].post("/api/shop-groups", json={"name": "A", "connection_ids": [str(world["a1"])]})).json()["groups"][0]["id"]
    settings = {"per_shop_per_day": 1, "window_start": "10:00", "window_end": "12:00", "spacing_minutes": 30, "stagger_minutes": 0}
    preview = (await world["a"].post(f"/api/scheduled/groups/{g}/reflow", json={"settings": settings})).json()
    assert len(preview["rows"]) == 2
    assert (await _times(world))[keys[0]] == "09:00"  # a preview saves nothing
    data = (await world["a"].get("/api/scheduled")).json()
    assert data["groups"][0]["settings"] is None
    applied = (await world["a"].post(f"/api/scheduled/groups/{g}/reflow", json={"settings": settings, "apply": True})).json()
    assert [r["new_local"] for r in applied["rows"]] == [r["new_local"] for r in preview["rows"]]
    data = {i["key"]: i for i in (await world["a"].get("/api/scheduled")).json()["items"]}
    assert data[keys[0]]["local_time"] == "10:00" and data[keys[1]]["local_time"] == "10:00"
    assert data[keys[0]]["local_day"] != data[keys[1]]["local_day"]  # one a day


async def test_another_account_gets_404(world) -> None:  # noqa: F811
    keys = await _slots(world, [(0, "a1", "09:00")])
    g = (await world["a"].post("/api/shop-groups", json={"name": "A", "connection_ids": [str(world["a1"])]})).json()["groups"][0]["id"]
    b = world["b"]
    assert (await b.get("/api/scheduled")).json()["items"] == []
    for path, body in [("/api/scheduled/preview", {"op": "shift", "keys": keys, "params": {"minutes": 5}}),
                       ("/api/scheduled/apply", {"op": "remove", "keys": keys})]:
        assert (await b.post(path, json=body)).status_code == 404
    assert (await b.put(f"/api/scheduled/groups/{g}", json={"paused": True})).status_code == 404
    assert (await b.post("/api/scheduled/restore", json={"undo": [{"key": keys[0], "at": datetime.now(timezone.utc).isoformat(), "state": "waiting"}]})).status_code == 404
    async with world["sm"]() as s:
        assert (await s.execute(select(PlannedSlot))).scalars().first().state == "waiting"
