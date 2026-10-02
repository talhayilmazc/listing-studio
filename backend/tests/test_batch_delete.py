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
