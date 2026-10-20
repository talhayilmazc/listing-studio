"""Every batch status against the real state of each listing in each shop it is meant for
(pipeline/batch_status.py, the one definition the Batches filter, its counts and every batch
badge use). Two shops: Frost Tees (a1) and Frost Mugs (a2).

The reported bug: a batch whose every listing is live showed as "half-made", because an
earlier abandoned draft of one of its listings (replaced by the draft that went live) counted
as a half-made listing. And "published" meant "anything is live": a batch with one listing of
two live was "published".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import (
    Asset,
    AssetStatus,
    DraftAttempt,
    GeneratedContent,
    GroupPlan,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    ListingSnapshot,
    PlannedSlot,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline.batch_status import HEADLINE, STATUSES, batch_statuses, headline
from tests.test_multi_shop import world  # noqa: F401  (fixture)

NOW = datetime.now(timezone.utc)


async def _batch(s, world, status=UploadBatchStatus.ready) -> UploadBatch:  # noqa: ANN001, F811
    b = UploadBatch(tenant_id=world["alice"], status=status, file_count=1, connection_id=world["a1"])
    s.add(b)
    await s.flush()
    return b


async def _listing(s, world, batch, name: str, *, approved=True, shops=None, written=True) -> GeneratedContent | None:  # noqa: ANN001, F811
    a = Asset(tenant_id=world["alice"], batch_id=batch.id, original_filename=f"{name}.png", storage_key=f"k-{name}",
              status=AssetStatus.processed, rank=1, group_key=name, parsed_sku=name)
    s.add(a)
    await s.flush()
    if not written:
        return None
    c = GeneratedContent(tenant_id=world["alice"], batch_id=batch.id, asset_id=a.id, listing_profile_id=world["pa1"],
                         title=f"{name} tee", tags=["t"] * 13, description="d", approved=approved,
                         target_shops=[str(x) for x in shops] if shops is not None else None)
    s.add(c)
    await s.flush()
    return c


_lid = iter(range(70_000, 80_000))


def _pub(s, world, content, shop, state="active", **kw):  # noqa: ANN001, F811
    s.add(ListingPublication(tenant_id=world["alice"], content_id=content.id, connection_id=shop,
                             etsy_listing_id=next(_lid), state=state, **kw))


async def test_every_status_against_each_listing_in_each_shop(world) -> None:  # noqa: F811
    a1, a2 = world["a1"], world["a2"]
    both = [a1, a2]
    async with world["sm"]() as s:
        b = {}
        b["not_written"] = await _batch(s, world)
        await _listing(s, world, b["not_written"], "NW", written=False)
        b["in_review"] = await _batch(s, world)
        await _listing(s, world, b["in_review"], "IR", approved=False)
        b["approved"] = await _batch(s, world)
        await _listing(s, world, b["approved"], "AP")
        b["partly_drafted"] = await _batch(s, world)
        c = await _listing(s, world, b["partly_drafted"], "PD", shops=both)
        _pub(s, world, c, a1, "draft")
        b["drafted"] = await _batch(s, world)
        c = await _listing(s, world, b["drafted"], "DR", shops=both)
        _pub(s, world, c, a1, "draft")
        _pub(s, world, c, a2, "draft")
        b["scheduled"] = await _batch(s, world)
        c = await _listing(s, world, b["scheduled"], "SC")
        _pub(s, world, c, a1, "draft", scheduled_for=NOW + timedelta(days=1))
        b["planned"] = await _batch(s, world)
        c = await _listing(s, world, b["planned"], "PL", shops=[a2])
        plan = GroupPlan(tenant_id=world["alice"], batch_id=b["planned"].id, settings={})
        s.add(plan)
        await s.flush()
        s.add(PlannedSlot(tenant_id=world["alice"], plan_id=plan.id, content_id=c.id, connection_id=a2,
                          draft_at=NOW + timedelta(days=1), publish_at=NOW + timedelta(days=1, hours=2), state="waiting"))
        b["live_some_shops"] = await _batch(s, world)
        c = await _listing(s, world, b["live_some_shops"], "LS", shops=both)
        _pub(s, world, c, a1)
        _pub(s, world, c, a2, "draft")
        b["live_some_listings"] = await _batch(s, world)
        c = await _listing(s, world, b["live_some_listings"], "LL1")
        _pub(s, world, c, a1)
        await _listing(s, world, b["live_some_listings"], "LL2")
        b["published"] = await _batch(s, world)
        for name in ("PB1", "PB2"):
            c = await _listing(s, world, b["published"], name, shops=both)
            _pub(s, world, c, a1)
            _pub(s, world, c, a2)
        b["published_unwritten"] = await _batch(s, world)
        c = await _listing(s, world, b["published_unwritten"], "PU1")
        _pub(s, world, c, a1)
        await _listing(s, world, b["published_unwritten"], "PU2", written=False)
        # The reported bug: every listing live, one with an earlier abandoned draft replaced by the live one.
        b["published_replaced"] = await _batch(s, world)
        c = await _listing(s, world, b["published_replaced"], "RP")
        _pub(s, world, c, a1, published_at=NOW - timedelta(hours=1))
        job = Job(tenant_id=world["alice"], connection_id=a1, type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(c.id)}, batch_id=b["published_replaced"].id,
                  last_error="Etsy timed out while uploading images", finished_at=NOW - timedelta(hours=3))
        s.add(job)
        await s.flush()
        s.add(ListingSnapshot(tenant_id=world["alice"], listing_id=60_001, job_id=job.id,
                              payload={"operation": "create_draft", "listing_id": 60_001}, taken_at=NOW - timedelta(hours=3)))
        b["half_made"] = await _batch(s, world)
        c = await _listing(s, world, b["half_made"], "HM", shops=both)
        _pub(s, world, c, a1)
        s.add(DraftAttempt(tenant_id=world["alice"], content_id=c.id, connection_id=a2, etsy_listing_id=60_002, title=c.title))
        s.add(Job(tenant_id=world["alice"], connection_id=a2, type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(c.id)}, batch_id=b["half_made"].id, finished_at=NOW,
                  last_error="Etsy refused it while setting sizes, prices and SKU (it answered 400)"))
        b["failed_draft"] = await _batch(s, world)
        c = await _listing(s, world, b["failed_draft"], "FD")
        s.add(Job(tenant_id=world["alice"], connection_id=a1, type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(c.id)}, batch_id=b["failed_draft"].id, finished_at=NOW,
                  last_error="Processing profile is not set up in Frost Tees"))
        b["failed_live"] = await _batch(s, world)
        c = await _listing(s, world, b["failed_live"], "FL")
        _pub(s, world, c, a1, "draft")
        s.add(Job(tenant_id=world["alice"], connection_id=a1, type=JobType.publish_live, status=JobStatus.failed,
                  payload={"content_id": str(c.id)}, batch_id=b["failed_live"].id, finished_at=NOW, last_error="x"))
        b["deleted"] = await _batch(s, world)
        c = await _listing(s, world, b["deleted"], "DE")
        _pub(s, world, c, a1, "deleted_on_etsy", deleted_on_etsy_at=NOW)
        b["uploading"] = await _batch(s, world, UploadBatchStatus.uploading)
        b["broken"] = await _batch(s, world, UploadBatchStatus.failed)
        await s.commit()
        got = await batch_statuses(s, world["alice"], list(b.values()))

    want = {
        "not_written": ["not_written"],
        "in_review": ["in_review"],
        "approved": ["approved"],
        "partly_drafted": ["partly_drafted"],
        "drafted": ["drafted"],
        "scheduled": ["scheduled"],
        "planned": ["scheduled"],
        "live_some_shops": ["drafted", "partly_published"],
        "live_some_listings": ["approved", "partly_published"],
        "published": ["published"],
        "published_unwritten": ["not_written", "partly_published"],
        "published_replaced": ["published"],  # the bug: it was also "half_made"
        "half_made": ["partly_published", "half_made"],
        "failed_draft": ["failed"],
        "failed_live": ["drafted", "failed"],
        "deleted": ["approved", "deleted"],
        "uploading": ["uploading"],
        "broken": ["failed"],
    }
    assert {k: got[v.id] for k, v in b.items()} == want
    assert set().union(*got.values()) == set(STATUSES)  # every status shows up somewhere
    # The one badge: what needs the seller first, then how far it got.
    assert headline(want["half_made"]) == "half_made" and headline(want["live_some_listings"]) == "approved"
    assert headline(want["published"]) == "published" and headline([]) is None
    assert set(HEADLINE) == set(STATUSES)


async def test_every_place_shows_the_same_statuses(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        live = await _batch(s, world)
        c = await _listing(s, world, live, "X1")
        _pub(s, world, c, world["a1"])
        await _listing(s, world, live, "X2")
        await s.commit()
    listed = {x["id"]: x for x in (await world["a"].get("/api/batches")).json()}[str(live.id)]
    summary = (await world["a"].get(f"/api/batches/{live.id}/summary")).json()
    detail = (await world["a"].get(f"/api/batches/{live.id}")).json()
    for row in (listed, summary, detail):
        assert row["statuses"] == ["approved", "partly_published"] and row["headline"] == "approved"
    assert (await world["b"].get(f"/api/batches/{live.id}/summary")).status_code == 404
    assert str(live.id) not in {x["id"] for x in (await world["b"].get("/api/batches")).json()}
