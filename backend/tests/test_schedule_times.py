"""Every scheduled listing says when its draft is made and when it goes live (fix/onboarding-followups, Part J).

The Schedule page and the send summary showed only the go-live time. Now both carry one line, in the
seller's time zone (the account's): "Draft created at 1:41 PM · goes live at 3:41 PM CST" once the draft
exists, "Draft is created 2 hours before it goes live" until then, and listings sent as drafts with no
go-live time "Stay a draft in your shop. Nothing will publish it." (pipeline/schedule_times.py).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.db.models import ListingPublication, Tenant
from app.pipeline import reschedule as R
from app.pipeline import schedule_times as T
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_scheduled import ZONE, _at, _slots
from tests.test_send import SPREAD, _group, _preview, _send_to

CHI = "America/Chicago"


def test_the_lines_read_as_the_seller_reads_them() -> None:
    day = datetime(2026, 11, 20).date()
    draft, live = R.instant(day, time(13, 41), CHI), R.instant(day, time(15, 41), CHI)
    assert T.both(draft, live, CHI) == "Draft created at 1:41 PM · goes live at 3:41 PM CST"
    assert T.both(draft, live, CHI, verb="went live") == "Draft created at 1:41 PM · went live at 3:41 PM CST"
    # On different days: the dates too.
    assert T.both(draft - timedelta(days=1), live, CHI) == "Draft created at Nov 19, 1:41 PM · goes live at Nov 20, 3:41 PM CST"
    # In the seller's zone, not UTC: the same instant in Istanbul.
    assert T.both(draft, live, "Europe/Istanbul") == "Draft created at Nov 20, 10:41 PM · goes live at Nov 21, 12:41 AM +03"
    now = live - timedelta(hours=5)
    assert T.to_be_drafted(live, CHI, now) == "Draft is created 2 hours before it goes live"
    assert T.to_be_drafted(live, CHI, live - timedelta(minutes=30)) == "Draft is being created · goes live at 3:41 PM CST"
    assert T.STAYS_A_DRAFT == "Stays a draft in your shop. Nothing will publish it."


async def test_the_schedule_page_gives_both_times(world) -> None:  # noqa: F811
    planned = (await _slots(world, [(0, "a1", "15:41")]))[0]
    live = _at("15:41")
    async with world["sm"]() as s:
        pub = ListingPublication(tenant_id=world["alice"], content_id=world["contents"][1], connection_id=world["a2"],
                                 etsy_listing_id=4242, state="draft", scheduled_for=live,
                                 created_at=live - timedelta(hours=2))
        s.add(pub)
        await s.commit()
        drafted = f"pub:{pub.id}"
    items = {i["key"]: i for i in (await world["a"].get("/api/scheduled")).json()["items"]}
    assert items[planned]["times"] == "Draft is created 2 hours before it goes live"  # no draft yet
    abbr = items[drafted]["local"].split()[-1]
    assert items[drafted]["times"] == f"Draft created at 1:41 PM · goes live at 3:41 PM {abbr}"  # the account's zone
    assert abbr in ("CST", "CDT")


async def test_the_send_summary_says_each_listing_s_times(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        (await s.get(Tenant, world["alice"])).time_zone = ZONE
        await s.commit()
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    await _send_to(world, group_ids=[both])

    drafts = await _preview(world, "drafts")
    cells = [c for r in drafts["rows"] for c in r["cells"]]
    assert cells and all(c["times"] == "Stays a draft in your shop. Nothing will publish it." for c in cells)

    spread = await _preview(world, "spread", SPREAD)
    cells = [c for r in spread["rows"] for c in r["cells"] if c["state"] == "new"]
    assert cells and all(c["times"] == "Draft is created 2 hours before it goes live" for c in cells)
    assert "draft_at" not in str(spread)  # an internal field, never sent


async def test_a_draft_already_made_says_when(world) -> None:  # noqa: F811
    """A listing whose draft is in the shop and is spread out: its draft's time and its go-live time."""
    made = datetime.now(timezone.utc) - timedelta(hours=1)
    async with world["sm"]() as s:
        (await s.get(Tenant, world["alice"])).time_zone = ZONE
        s.add(ListingPublication(tenant_id=world["alice"], content_id=world["contents"][0], connection_id=world["a1"],
                                 etsy_listing_id=777, state="draft", created_at=made))
        await s.commit()
    await _send_to(world, connection_ids=[str(world["a1"])])
    spread = await _preview(world, "spread", SPREAD)
    cell = next(c for r in spread["rows"] if r["content_id"] == str(world["contents"][0]) for c in r["cells"])
    assert cell["state"] == "publish" and cell["times"].startswith("Draft created at ") and " · goes live at " in cell["times"]
    drafts = await _preview(world, "drafts")
    cell = next(c for r in drafts["rows"] if r["content_id"] == str(world["contents"][0]) for c in r["cells"])
    assert (cell["state"], cell["times"]) == ("draft", "Stays a draft in your shop. Nothing will publish it.")
