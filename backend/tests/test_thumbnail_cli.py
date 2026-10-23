"""``python -m app.cli thumbnails``: the screens' copies for images stored before them, the earlier
previews deleted, a dry run first, within the disk and storage budget (app/ops/thumbnails.py)."""

from __future__ import annotations

import uuid

from app.db import models as M
from app.ops import thumbnails
from app.pipeline import thumbs
from app.pipeline.storage import LocalStorage
from tests.auth_support import make_tenant
from tests.test_image_delivery import _jpeg, _seed


async def _two_accounts(async_sm, tmp_path):
    storage = LocalStorage(tmp_path / "store")
    alice = await make_tenant(async_sm, "alice@example.com")
    bob = await make_tenant(async_sm, "bob@example.com")
    a = await _seed(async_sm, storage, alice)
    b = await _seed(async_sm, storage, bob)
    # A preview of the earlier scheme beside each photo.
    for seeded in (a, b):
        storage.put(f"{seeded['asset_key']}.v2.w224.jpg", b"x" * 1000, "image/jpeg")
        storage.put(f"{seeded['photo_key']}.w448.jpg", b"x" * 2000, "image/jpeg")
    return storage, alice, bob, a, b


async def test_a_dry_run_counts_and_estimates_and_writes_nothing(async_sm, tmp_path) -> None:
    storage, *_ , a, _b = await _two_accounts(async_sm, tmp_path)
    before = sorted(k for k, _, _ in storage.files())
    out = await thumbnails.run(async_sm, storage, apply=False)
    # Per account: a batch photo (small, thumb), a template photo (small, thumb, medium), a mockup (small).
    assert "12 cop(ies) to make" in out and "4 earlier preview(s)" in out and "dry run" in out, out
    assert "about " in out and "disk free now" in out
    assert sorted(k for k, _, _ in storage.files()) == before


async def test_apply_makes_the_copies_and_deletes_the_earlier_previews(async_sm, tmp_path) -> None:
    storage, alice, bob, a, b = await _two_accounts(async_sm, tmp_path)
    out = await thumbnails.run(async_sm, storage, apply=True)
    assert "made 12 cop(ies)" in out and "deleted 4 earlier preview(s)" in out, out
    for seeded in (a, b):
        assert all(storage.exists(thumbs.key_for(seeded["asset_key"], s)) for s in ("small", "thumb"))
        assert all(storage.exists(thumbs.key_for(seeded["photo_key"], s)) for s in ("small", "thumb", "medium"))
        assert storage.exists(thumbs.key_for(seeded["output_key"], "small"))
        assert not storage.exists(f"{seeded['asset_key']}.v2.w224.jpg")
        assert storage.exists(seeded["asset_key"]) and storage.exists(seeded["photo_key"])  # the files themselves stay
    again = await thumbnails.run(async_sm, storage, apply=False)
    assert "every copy is already made" in again and "0 earlier preview(s)" in again, again


async def test_one_account_only(async_sm, tmp_path) -> None:
    storage, alice, bob, a, b = await _two_accounts(async_sm, tmp_path)
    out = await thumbnails.run(async_sm, storage, apply=True, email="alice@example.com")
    assert "made 6 cop(ies)" in out, out
    assert storage.exists(thumbs.key_for(a["asset_key"], "thumb")) and not storage.exists(thumbs.key_for(b["asset_key"], "thumb"))
    assert storage.exists(f"{b['asset_key']}.v2.w224.jpg")  # bob's earlier previews untouched
    assert "no account" in await thumbnails.run(async_sm, storage, apply=False, email="nobody@example.com")


async def test_an_account_at_its_storage_cap_is_left_out(async_sm, tmp_path) -> None:
    storage, alice, bob, a, b = await _two_accounts(async_sm, tmp_path)
    async with async_sm() as s:
        (await s.get(M.Tenant, bob)).storage_cap_bytes = 1
        await s.commit()
    out = await thumbnails.run(async_sm, storage, apply=True)
    assert "made 6 cop(ies)" in out and "left out 1 account(s)" in out and "bob@example.com" in out, out
    assert not storage.exists(thumbs.key_for(b["asset_key"], "thumb"))


async def test_it_stops_when_the_disk_would_be_too_full(async_sm, tmp_path) -> None:
    storage, *_ = await _two_accounts(async_sm, tmp_path)
    out = await thumbnails.run(async_sm, storage, apply=True, keep_free_gb=10**9)
    assert "made 0 cop(ies)" in out and "stopped: less than" in out, out


async def test_a_missing_source_is_counted_not_fatal(async_sm, tmp_path) -> None:
    storage, alice, bob, a, b = await _two_accounts(async_sm, tmp_path)
    async with async_sm() as s:
        batch = await s.get(M.UploadBatch, a["batch"])
        s.add(M.Asset(batch_id=batch.id, tenant_id=alice, original_filename="x.jpg", processed_key=f"{alice}/gone/{uuid.uuid4()}.jpg",
                      mime_type="image/jpeg", rank=2, status=M.AssetStatus.processed))
        await s.commit()
    storage.put(f"{alice}/extra/{uuid.uuid4()}.jpg", _jpeg(10, 10), "image/jpeg")
    out = await thumbnails.run(async_sm, storage, apply=True)
    assert "made 12 cop(ies)" in out, out  # the missing file has nothing to copy and is skipped
