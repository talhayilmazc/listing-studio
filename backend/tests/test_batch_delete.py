"""Deleting batches (v7 §E3): uploads and content go, Etsy drafts are not touched."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.api import deps
from app.db.models import Asset, GeneratedContent, Job, JobStatus, JobType, ListingPublication, UploadBatch
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
        assert (await s.execute(select(ListingPublication))).scalars().all() == []
        [job] = (await s.execute(select(Job))).scalars().all()
        assert job.status is JobStatus.cancelled  # queued work for it will not run
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
