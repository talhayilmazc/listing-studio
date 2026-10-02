"""Deleting batches (v7 §E3): uploads and content go, Etsy drafts are not touched."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.api import deps
from app.db.models import (
    Asset, AuditLog, GeneratedContent, Job, JobStatus, JobType, ListingPublication, ListingSnapshot, UploadBatch,
)
from app.pipeline.storage import LocalStorage
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)


async def _batch_with_files(ctx, tmp_path, *, listing_id=None):  # noqa: F811
    storage = LocalStorage(tmp_path)
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: storage
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=listing_id)
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        batch_id = content.batch_id
        asset = await s.get(Asset, content.asset_id)
        base = f"{ctx['tenant_id']}/{batch_id}"
        asset.storage_key, asset.processed_key = f"{base}/original/a.png", f"{base}/processed/a.jpg"
        s.add(Job(tenant_id=ctx["tenant_id"], connection_id=await _shop(s, ctx["tenant_id"]), type=JobType.create_draft,
                  payload={"content_id": str(content_id)}, batch_id=batch_id, status=JobStatus.queued))
        await s.commit()
    for key in ("original/a.png", "processed/a.jpg", "processed/a.jpg.w224.jpg"):
        storage.put(f"{base}/{key}", b"x")
    storage.put(f"{ctx['tenant_id']}/other-batch/keep.jpg", b"x")
    return batch_id, content_id, tmp_path


async def test_deleting_a_batch_removes_uploads_and_content_but_not_etsy_drafts(ctx, tmp_path) -> None:  # noqa: F811
    batch_id, content_id, root = await _batch_with_files(ctx, tmp_path, listing_id=777)
    resp = await ctx["client"].delete(f"/api/batches/{batch_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1, "files_removed": 1, "listings_left_on_etsy": 1, "jobs_cancelled": 1}
    async with ctx["sm"]() as s:
        assert await s.get(UploadBatch, batch_id) is None
        assert await s.get(GeneratedContent, content_id) is None
        # The record that a listing was created (and published) outlives the batch:
        # detached from the content, with what it was still on the row.
        [publication] = (await s.execute(select(ListingPublication))).scalars().all()
        assert publication.content_id is None and publication.etsy_listing_id == 777
        [job] = (await s.execute(select(Job))).scalars().all()
        assert job.status is JobStatus.cancelled  # queued work for it will not run
        [entry] = (await s.execute(select(AuditLog).where(AuditLog.action == "batch.deleted"))).scalars().all()
        assert entry.details["publications_kept"] == 1 and entry.details["batch_id"] == str(batch_id)
    assert not (root / str(ctx["tenant_id"]) / str(batch_id)).exists()
    assert (root / str(ctx["tenant_id"]) / "other-batch" / "keep.jpg").exists()
    # Nothing was sent to Etsy: no job queued, no request made.
    assert ctx["enqueuer"].calls == []


async def test_bulk_delete_is_all_or_nothing_across_accounts(ctx, tmp_path) -> None:  # noqa: F811
    mine, _, _ = await _batch_with_files(ctx, tmp_path)
    other = await make_tenant(ctx["sm"], "other@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    resp = await ctx["client"].post("/api/batch-actions/delete", json={"batch_ids": [str(mine)]})
    assert resp.status_code == 404
    assert (await ctx["client"].delete(f"/api/batches/{mine}")).status_code == 404
    async with ctx["sm"]() as s:
        assert await s.get(UploadBatch, mine) is not None


def test_storage_never_deletes_outside_a_batch_folder(tmp_path) -> None:
    import pytest

    storage = LocalStorage(tmp_path)
    for bad in ("", "/", "..", "a/../..", "../elsewhere"):
        with pytest.raises(ValueError):
            storage.delete_prefix(bad)
    storage.delete_prefix(f"{uuid.uuid4()}/{uuid.uuid4()}")  # absent folder: no error


async def test_published_history_and_counts_survive_deleting_every_batch(ctx, tmp_path) -> None:  # noqa: F811
    """The production report: sellers publish, delete their batches, and the app said 0 published."""
    from datetime import datetime, timedelta, timezone

    batch_id, content_id, _ = await _batch_with_files(ctx, tmp_path, listing_id=888)
    async with ctx["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalars().one()
        publication.state, publication.title, publication.sku = "active", "A Title", "BR5475"
        publication.published_at = datetime.now(timezone.utc)
        await s.commit()
    assert (await ctx["client"].delete(f"/api/batches/{batch_id}")).status_code == 200
    async with ctx["sm"]() as s:
        assert (await s.execute(select(UploadBatch))).scalars().all() == []
        kept = (await s.execute(select(ListingPublication))).scalars().one()
        assert (kept.state, kept.title, kept.sku, kept.content_id) == ("active", "A Title", "BR5475", None)
        assert kept.published_at is not None

    # A draft waiting on a schedule: the schedule is cancelled with its batch,
    # and nothing is left holding back budget for a go-live that can never run.
    batch_id, content_id, _ = await _batch_with_files(ctx, tmp_path, listing_id=889)
    async with ctx["sm"]() as s:
        draft = (await s.execute(select(ListingPublication).where(ListingPublication.etsy_listing_id == 889))).scalars().one()
        draft.scheduled_for = datetime.now(timezone.utc) + timedelta(hours=3)
        await s.commit()
    assert (await ctx["client"].delete(f"/api/batches/{batch_id}")).status_code == 200
    async with ctx["sm"]() as s:
        draft = (await s.execute(select(ListingPublication).where(ListingPublication.etsy_listing_id == 889))).scalars().one()
        assert draft.scheduled_for is None and "batch was deleted" in draft.schedule_note
    assert (await ctx["client"].get("/api/schedules")).json() == []


async def test_lost_publication_records_are_rebuilt_from_the_draft_snapshots(ctx) -> None:  # noqa: F811
    from datetime import datetime, timezone

    from app.cli import rebuild_publications

    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        made = Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.create_draft, payload={}, status=JobStatus.succeeded)
        live = Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.publish_live, payload={}, status=JobStatus.succeeded,
                   finished_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
        failed = Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.publish_live, payload={}, status=JobStatus.failed)
        s.add_all([made, live, failed])
        await s.flush()
        for listing_id in (501, 502):
            s.add(ListingSnapshot(tenant_id=ctx["tenant_id"], listing_id=listing_id, job_id=made.id,
                                  payload={"operation": "create_draft", "submitted": {"title": f"Title {listing_id}"}}))
        s.add(ListingSnapshot(tenant_id=ctx["tenant_id"], listing_id=501, job_id=live.id, payload={"operation": "publish_live"}))
        s.add(ListingSnapshot(tenant_id=ctx["tenant_id"], listing_id=502, job_id=failed.id, payload={"operation": "publish_live"}))
        # One whose record was never lost is left alone.
        s.add(ListingSnapshot(tenant_id=ctx["tenant_id"], listing_id=503, job_id=made.id, payload={"operation": "create_draft"}))
        s.add(ListingPublication(tenant_id=ctx["tenant_id"], content_id=None, connection_id=shop, etsy_listing_id=503, state="draft"))
        await s.commit()

    assert "would rebuild 2" in await rebuild_publications(ctx["sm"], apply=False)
    async with ctx["sm"]() as s:
        assert len((await s.execute(select(ListingPublication))).scalars().all()) == 1  # a dry run writes nothing
    assert "rebuilt 2" in await rebuild_publications(ctx["sm"], apply=True)
    async with ctx["sm"]() as s:
        rows = {p.etsy_listing_id: p for p in (await s.execute(select(ListingPublication))).scalars()}
    assert (rows[501].state, rows[501].title, rows[501].published_at.day) == ("active", "Title 501", 20)
    assert (rows[502].state, rows[502].published_at) == ("draft", None)  # its go-live failed
    assert "rebuild 0" in await rebuild_publications(ctx["sm"], apply=False)  # and it does not repeat itself


# --- deleting one image of a group -----------------------------------------------------------------


async def _group_of_three(ctx, tmp_path, *, listing_id=None):  # noqa: F811
    """One listing group: a cover (with the listing's content and a saved crop) and two more images."""
    from app.db.models import AssetStatus, ListingGroupSetting

    storage = LocalStorage(tmp_path)
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: storage
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=listing_id)
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        batch_id = content.batch_id
        base = f"{ctx['tenant_id']}/{batch_id}"
        cover = await s.get(Asset, content.asset_id)
        cover.group_key, cover.rank, cover.cover_crop = "G1", 1, {"x": 0.1, "y": 0.1, "size": 0.5}
        cover.storage_key, cover.processed_key = f"{base}/original/a.png", f"{base}/processed/a.jpg"
        ids = [cover.id]
        for rank, name in ((2, "b"), (3, "c")):
            extra = Asset(batch_id=batch_id, tenant_id=ctx["tenant_id"], original_filename=f"G1/{name}.png", group_key="G1",
                          storage_key=f"{base}/original/{name}.png", processed_key=f"{base}/processed/{name}.jpg",
                          status=AssetStatus.processed, rank=rank)
            s.add(extra)
            await s.flush()
            ids.append(extra.id)
        s.add(ListingGroupSetting(tenant_id=ctx["tenant_id"], batch_id=batch_id, group_key="G1"))
        await s.commit()
    for name in ("a", "b", "c"):
        storage.put(f"{base}/original/{name}.png", b"x")
        storage.put(f"{base}/processed/{name}.jpg", b"x")
        storage.put(f"{base}/processed/{name}.jpg.v1.w224.jpg", b"x")
    return batch_id, content_id, ids, tmp_path / str(ctx["tenant_id"]) / str(batch_id)


async def test_deleting_the_cover_makes_the_next_image_the_cover_and_keeps_the_listing(ctx, tmp_path) -> None:  # noqa: F811
    batch_id, content_id, (a, b, c), root = await _group_of_three(ctx, tmp_path, listing_id=901)
    resp = await ctx["client"].delete(f"/api/assets/{a}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["group_removed"], body["cover_changed"], body["listings_on_etsy"]) == (False, True, 1)
    assert [x["id"] for x in sorted(body["batch"]["assets"], key=lambda x: x["rank"])] == [str(b), str(c)]
    async with ctx["sm"]() as s:
        assert await s.get(Asset, a) is None
        content = await s.get(GeneratedContent, content_id)
        assert content is not None and content.asset_id == b  # the listing is about the design, not one photo
        cover = await s.get(Asset, b)
        assert (cover.rank, cover.cover_crop) == (1, None) and (await s.get(Asset, c)).rank == 2
        # The draft on Etsy is not touched, and the record of it stays attached.
        assert (await s.execute(select(ListingPublication))).scalars().one().content_id == content_id
    # Its files, and the previews made from them, are gone; the other images' are not.
    assert sorted(p.name for p in (root / "processed").iterdir()) == ["b.jpg", "b.jpg.v1.w224.jpg", "c.jpg", "c.jpg.v1.w224.jpg"]
    assert sorted(p.name for p in (root / "original").iterdir()) == ["b.png", "c.png"]
    assert ctx["enqueuer"].calls == []  # nothing was sent to Etsy


async def test_deleting_another_image_leaves_the_cover_and_its_crop(ctx, tmp_path) -> None:  # noqa: F811
    _, content_id, (a, b, c), _ = await _group_of_three(ctx, tmp_path)
    body = (await ctx["client"].delete(f"/api/assets/{b}")).json()
    assert (body["group_removed"], body["cover_changed"], body["listings_on_etsy"]) == (False, False, 0)
    async with ctx["sm"]() as s:
        cover = await s.get(Asset, a)
        assert cover.rank == 1 and cover.cover_crop is not None and (await s.get(Asset, c)).rank == 2
        assert (await s.get(GeneratedContent, content_id)).asset_id == a


async def test_deleting_the_last_image_removes_the_group_and_says_so(ctx, tmp_path) -> None:  # noqa: F811
    from app.db.models import ListingGroupSetting

    batch_id, content_id, (a, b, c), _ = await _group_of_three(ctx, tmp_path, listing_id=902)
    async with ctx["sm"]() as s:
        s.add(Job(tenant_id=ctx["tenant_id"], connection_id=await _shop(s, ctx["tenant_id"]), type=JobType.publish_live,
                  payload={"content_id": str(content_id)}, batch_id=batch_id, status=JobStatus.queued))
        await s.commit()
    for asset_id in (c, b):
        assert (await ctx["client"].delete(f"/api/assets/{asset_id}")).json()["group_removed"] is False
    body = (await ctx["client"].delete(f"/api/assets/{a}")).json()
    assert (body["group_removed"], body["listings_on_etsy"], body["batch"]["assets"]) == (True, 1, [])
    async with ctx["sm"]() as s:
        assert await s.get(GeneratedContent, content_id) is None
        assert (await s.execute(select(ListingGroupSetting))).scalars().all() == []
        # What was made on Etsy from it stays on record, detached; queued work for it will not run.
        kept = (await s.execute(select(ListingPublication))).scalars().one()
        assert kept.content_id is None and kept.etsy_listing_id == 902
        assert (await s.execute(select(Job))).scalars().one().status is JobStatus.cancelled
        assert await s.get(UploadBatch, batch_id) is not None  # the batch itself stays


async def test_an_image_can_only_be_deleted_by_its_owner(ctx, tmp_path) -> None:  # noqa: F811
    _, _, (a, _, _), root = await _group_of_three(ctx, tmp_path)
    other = await make_tenant(ctx["sm"], "other-img@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    assert (await ctx["client"].delete(f"/api/assets/{a}")).status_code == 404
    async with ctx["sm"]() as s:
        assert await s.get(Asset, a) is not None
    assert (root / "processed" / "a.jpg").exists()


def test_deleting_one_stored_file_takes_its_previews_and_nothing_else(tmp_path) -> None:
    import pytest

    storage = LocalStorage(tmp_path)
    for key in ("t/b/processed/a.jpg", "t/b/processed/a.jpg.v1.w224.jpg", "t/b/processed/a.jpg2", "t/b/processed/ab.jpg"):
        storage.put(key, b"x")
    storage.delete_with_derivatives("t/b/processed/a.jpg")
    assert sorted(p.name for p in (tmp_path / "t/b/processed").iterdir()) == ["a.jpg2", "ab.jpg"]
    storage.delete_with_derivatives("t/b/processed/missing.jpg")  # absent: no error
    for bad in ("", "../x", "t/../../x"):
        with pytest.raises(ValueError):
            storage.delete_with_derivatives(bad)


# --- "Published this month" is the app's own record ---------------------------------------------


async def test_published_this_month_counts_the_publication_records_not_the_listing_cache(ctx, tmp_path) -> None:  # noqa: F811
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    batch_id, _, _ = await _batch_with_files(ctx, tmp_path, listing_id=920)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        first = (await s.execute(select(ListingPublication))).scalars().one()
        first.state, first.published_at = "active", now
        last_month = now.replace(day=1) - timedelta(days=3)
        s.add(ListingPublication(tenant_id=ctx["tenant_id"], content_id=None, connection_id=shop, etsy_listing_id=921,
                                 state="active", published_at=last_month))
        s.add(ListingPublication(tenant_id=ctx["tenant_id"], content_id=None, connection_id=shop, etsy_listing_id=922, state="draft"))
        await s.commit()

    # The listing cache is empty: the shop-wide counts are unknown, not zero, and a refresh is queued.
    body = (await ctx["client"].get("/api/shop/summary")).json()
    assert (body["app_published_this_month"], body["app_published_last_month"]) == (1, 1)
    assert (body["shop_counts_known"], body["syncing"]) == (False, True)
    assert ctx["enqueuer"].calls[-1][0] == "sync_shop_listings"

    # And it still says 1 after the seller deletes the batch it was published from.
    assert (await ctx["client"].delete(f"/api/batches/{batch_id}")).status_code == 200
    assert (await ctx["client"].get("/api/shop/summary")).json()["app_published_this_month"] == 1
