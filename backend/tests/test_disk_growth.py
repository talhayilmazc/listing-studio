"""How fast the disk fills: daily readings, the rate, days until full, and
storage per account in Admin > Usage. Sizes only."""

from __future__ import annotations

import time

from app.core import disk, storage_cap
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_upload_retention import IMAGE, _listing, _storage

GB = 1024**3


def _day(n: int, used: float, storage: float = 0) -> dict:
    return {"day": f"2026-10-{n:02d}", "used": int(used * GB), "free": int((30 - used) * GB), "storage": int(storage * GB)}


def test_days_until_full_is_free_space_over_the_last_weeks_rate() -> None:
    readings = [_day(1, 15, 8), _day(2, 16, 9), _day(8, 21.1, 10.9)]  # 6.1 GB in 7 days
    f = disk.forecast(readings, free_now=int(8.9 * GB))
    assert f.basis_days == 7 and abs(f.per_day - 6.1 * GB / 7) < 1 and abs(f.days_left - 8.9 / (6.1 / 7)) < 0.01
    assert abs(f.storage_per_day - 2.9 * GB / 7) < 1
    # Only the last seven days count.
    older = [_day(1, 1), *[_day(n, 20 + 0.1 * n) for n in range(2, 12)]]
    assert disk.forecast(older, int(9 * GB)).basis_days == 7
    # One reading, or a disk that is not filling: no forecast.
    assert disk.forecast([_day(1, 15)], int(9 * GB)).days_left is None
    assert disk.forecast([_day(1, 16), _day(2, 15)], int(9 * GB)).days_left is None


async def test_one_reading_a_day_is_kept_for_30_days(world) -> None:  # noqa: F811
    snap = disk.Snapshot(at=time.time(), total_bytes=30 * GB, free_bytes=9 * GB, categories=[
        disk.Category("uploads", 1 * GB), disk.Category("derivatives", 2 * GB)], host_at=None, host_fresh=False)
    async with world["sm"]() as s:
        from datetime import date, timedelta

        for n in range(40):
            await disk.record_history(s, snap, (date(2026, 9, 1) + timedelta(days=n)).isoformat())
        await disk.record_history(s, snap, "2026-10-10")  # the same day again replaces it
        readings = await disk.history(s)
    assert len(readings) == disk.HISTORY_DAYS and readings[-1] == {"day": "2026-10-10", "used": 21 * GB, "free": 9 * GB, "storage": 3 * GB}


async def test_admin_usage_shows_storage_per_account_and_days_until_full(world) -> None:  # noqa: F811
    storage = _storage(world)
    from datetime import timedelta

    await _listing(world, uploaded=timedelta(0), images=2)
    bob = world["bob"]
    async with world["sm"]() as s:
        from app.db.models import AppSetting

        s.add(AppSetting(key=disk.HISTORY_KEY, value=[_day(1, 19), _day(8, 21)]))
        await s.commit()
    out = (await world["a"].get("/api/admin/disk")).json()
    mine = next(a for a in out["accounts"] if a["tenant_id"] == str(bob.tenant_id))
    assert mine["email"] and "@" in mine["email"] and mine["bytes"] >= 4 * len(IMAGE) and mine["cap_bytes"] == 5 * storage_cap.GB
    assert out["growth_basis_days"] == 7 and abs(out["growth_per_day"] - 2 * GB / 7) < 1
    assert out["days_until_full"] is not None and out["days_until_full"] > 0
    assert (await world["b"].get("/api/admin/disk")).status_code == 404
    assert storage is not None
