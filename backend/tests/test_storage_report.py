"""python -m app.cli storage-report: per account and kind, sizes, ages, and
whether the listing group still needs the files. No names, no personal data."""

from __future__ import annotations

import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import update

from app.db.models import GroupPlan, ListingPublication, PlannedSlot
from app.pipeline import group_state as gs
from app.pipeline import storage_report as sr
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_upload_retention import DAY, IMAGE, NOW, _listing, _settle, _storage


def test_files_are_sorted_by_kind_and_age() -> None:
    t, b, a = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert sr.kind_of(f"{t}/{b}/original/{a}.png") == "original"
    assert sr.kind_of(f"{t}/{b}/processed/{a}.jpg") == "processed"
    assert sr.kind_of(f"{t}/{b}/processed/{a}.jpg.v3.w224.jpg") == "preview"
    assert sr.kind_of(f"{t}/{b}/kept/{a}.jpg") == "kept cover"
    assert sr.kind_of("stray.txt") is None
    assert [sr.age_label(d) for d in (0.5, 2, 5, 10, 20, 40)] == ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30d+"]


async def test_the_report_counts_by_account_kind_age_and_state(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    drafted = await _listing(world, uploaded=5 * DAY, written=5 * DAY, drafted=4 * DAY, group="DONE")
    review = await _listing(world, uploaded=2 * DAY, written=2 * DAY, group="REVIEW", images=2)
    scheduled = await _listing(world, uploaded=2 * DAY, written=2 * DAY, drafted=1 * DAY, group="SOON", images=1)
    bob = world["bob"]
    async with world["sm"]() as s:
        # A go-live waits for the scheduled one; a planned draft for another shop of the drafted one is gone.
        await s.execute(update(ListingPublication).where(ListingPublication.id == scheduled["publication_id"])
                        .values(scheduled_for=NOW + DAY))
        await s.commit()
    # Make the drafted group's files 5 days old.
    for pair in drafted["keys"]:
        for key in pair:
            os.utime(storage._path(key), (time.time() - 5 * 86400,) * 2)
    async with world["sm"]() as s:
        states = await gs.group_states(s)
        report = await sr.build(s, storage)
    by_group = {key[1]: g for key, g in states.items()}
    assert by_group["DONE"].state == gs.DRAFTED and by_group["DONE"].targets == {bob.connection_id}
    assert by_group["REVIEW"].state == gs.IN_REVIEW and by_group["SOON"].state == gs.PENDING

    mine = report.accounts[str(bob.tenant_id)[:8]]
    original, processed, preview = mine["original"], mine["processed"], mine["preview"]
    assert (original.files, original.bytes, original.average) == (6, 6 * len(IMAGE), len(IMAGE))
    assert processed.states[gs.DRAFTED] == 3 * len(IMAGE) and processed.states[gs.IN_REVIEW] == 2 * len(IMAGE)
    assert processed.states[gs.PENDING] == len(IMAGE)
    assert original.ages["3-7d"] == 3 * len(IMAGE) and original.ages["<1d"] == 3 * len(IMAGE)
    assert preview.files == 6 and preview.bytes == 6 * 500

    text = sr.render(report)
    assert f"Account {str(bob.tenant_id)[:8]}…" in text and str(bob.tenant_id) not in text
    assert "drafted in every target shop" in text and "still in review" in text and "scheduled / pending" in text
    assert "BR" not in text and "DONE" not in text and ".png" not in text  # no names


async def test_a_group_meant_for_two_shops_is_drafted_only_when_both_have_it(world) -> None:  # noqa: F811
    await _settle(world)
    bob = world["bob"]
    listing = await _listing(world, uploaded=5 * DAY, written=5 * DAY, drafted=4 * DAY, group="TWO")
    other = uuid.uuid4()
    async with world["sm"]() as s:
        from app.db.models import ConnectionStatus, EtsyConnection

        s.add(EtsyConnection(id=other, tenant_id=bob.tenant_id, status=ConnectionStatus.active, etsy_user_id=77001, shop_id=77001))
        plan = GroupPlan(tenant_id=bob.tenant_id, settings={})
        s.add(plan)
        await s.flush()
        slot = PlannedSlot(tenant_id=bob.tenant_id, plan_id=plan.id, content_id=listing["content_id"], connection_id=other,
                           draft_at=NOW, publish_at=NOW + timedelta(hours=2), state="scheduled")
        s.add(slot)
        await s.commit()
        g = (await gs.group_states(s))[(listing["batch_id"], "TWO")]
    # The finished slot does not hold the files, but the shop it was for has no draft yet.
    assert g.state == gs.IN_REVIEW and g.targets == {bob.connection_id, other}
