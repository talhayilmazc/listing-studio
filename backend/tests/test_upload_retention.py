"""Upload retention: image files go a set time after their listing is published
(or after a group nobody published was last worked on); every record stays, a
cover thumbnail is kept, and the disk they were on is accounted for."""

from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select, update

from app.api import deps, publish
from app.core import alerts, disk
from app.core.config import set_settings_override
from app.db.models import (
    AppSetting,
    Asset,
    AssetStatus,
    AuditLog,
    GeneratedContent,
    Job,
    JobStatus,
    ListingPublication,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import upload_retention
from app.pipeline.storage import LocalStorage
from app.workers import upkeep
from tests.support import VALID_TITLE
from tests.test_admin import world  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _image(size=(900, 1100), color=(40, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


IMAGE = _image()


def _storage(world) -> LocalStorage:  # noqa: F811
    return world["app"].dependency_overrides[deps.get_storage]()


async def _listing(
    world,  # noqa: F811
    *,
    uploaded: timedelta,
    images: int = 3,
    written: timedelta | None = None,
    drafted: timedelta | None = None,
    published: timedelta | None = None,
    group: str = "BR100",
    batch_id: uuid.UUID | None = None,
) -> dict:
    """One listing group of bob's, each event that long before NOW: the files
    as the app stores them (original, processed copy, a cached preview), and
    optionally its text, its draft and its publication."""
    bob, storage = world["bob"], _storage(world)
    async with world["sm"]() as s:
        if batch_id is None:
            batch = UploadBatch(tenant_id=bob.tenant_id, status=UploadBatchStatus.ready, file_count=images, created_at=NOW - uploaded)
            s.add(batch)
            await s.flush()
            batch_id = batch.id
        assets = []
        for i in range(images):
            asset_id = uuid.uuid4()
            original = f"{bob.tenant_id}/{batch_id}/original/{asset_id}.png"
            processed = f"{bob.tenant_id}/{batch_id}/processed/{asset_id}.jpg"
            storage.put(original, IMAGE)
            storage.put(processed, IMAGE)
            storage.put(f"{processed}.v3.w224.jpg", b"p" * 500)
            assets.append(Asset(
                id=asset_id, tenant_id=bob.tenant_id, batch_id=batch_id, original_filename=f"{group}_{i + 1}.png", parsed_sku=group,
                group_key=group, rank=i + 1, status=AssetStatus.processed, storage_key=original, processed_key=processed,
                mime_type="image/jpeg", width=900, height=1100, byte_size=len(IMAGE),
            ))
        s.add_all(assets)
        await s.flush()
        content_id = publication_id = None
        if written is not None:
            content = GeneratedContent(
                tenant_id=bob.tenant_id, batch_id=batch_id, asset_id=assets[0].id, listing_profile_id=bob.profile_id,
                title=VALID_TITLE, tags=[f"tag{i}" for i in range(13)], description="A description.", approved=True,
                created_at=NOW - written,
            )
            s.add(content)
            await s.flush()
            content_id = content.id
            if drafted is not None:
                publication = ListingPublication(
                    tenant_id=bob.tenant_id, content_id=content.id, connection_id=bob.connection_id, etsy_listing_id=int(time.time_ns() % 10**9),
                    title=VALID_TITLE, sku=group, created_at=NOW - drafted,
                    state="active" if published is not None else "draft",
                    published_at=NOW - published if published is not None else None,
                )
                s.add(publication)
                await s.flush()
                publication_id = publication.id
        await s.commit()
        return {"batch_id": batch_id, "assets": [a.id for a in assets], "content_id": content_id, "publication_id": publication_id,
                "keys": [(a.storage_key, a.processed_key) for a in assets]}


async def _settle(world) -> None:  # noqa: F811
    """The seed leaves a queued job on each account's own batch; finish them."""
    async with world["sm"]() as s:
        await s.execute(update(Job).values(status=JobStatus.succeeded))
        await s.commit()


def _left(storage: LocalStorage, listing: dict) -> list[str]:
    return [key for pair in listing["keys"] for key in pair if storage.exists(key)]


DAY = timedelta(days=1)


async def test_files_go_14_days_after_publishing_and_every_record_stays(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=15 * DAY)
    recent = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=13 * DAY, group="BR200")
    async with world["sm"]() as s:
        result = await upload_retention.run(s, storage, now=NOW)

    assert (result.published_groups, result.unpublished_groups, result.images, result.applied) == (1, 0, 3, True)
    # Three images: the original, the processed copy and its cached preview each.
    assert result.files == 9 and result.freed_bytes == 3 * (2 * len(IMAGE) + 500)
    assert _left(storage, old) == [] and not storage.exists(f"{old['keys'][0][1]}.v3.w224.jpg")
    assert len(_left(storage, recent)) == 6  # published 13 days ago: not yet

    async with world["sm"]() as s:
        rows = {a.id: a for a in (await s.execute(select(Asset).where(Asset.batch_id == old["batch_id"]))).scalars()}
        assert len(rows) == 3 and all(a.files_removed_at is not None and a.processed_key is None for a in rows.values())
        # The cover keeps a small JPEG; the other photos keep nothing.
        cover = rows[old["assets"][0]]
        assert cover.thumbnail_key == f"{world['bob'].tenant_id}/{old['batch_id']}/kept/{cover.id}.jpg"
        assert [rows[i].thumbnail_key for i in old["assets"][1:]] == [None, None]
        kept = Image.open(io.BytesIO(storage.get(cover.thumbnail_key)))
        assert kept.format == "JPEG" and kept.width == upload_retention.THUMBNAIL_WIDTH
        assert (result.thumbnails, result.thumbnail_bytes) == (1, len(storage.get(cover.thumbnail_key)))
        # The listing text and the record that it was published are untouched.
        content = await s.get(GeneratedContent, old["content_id"])
        publication = await s.get(ListingPublication, old["publication_id"])
        assert content.title == VALID_TITLE and content.asset_id == cover.id
        assert publication.content_id == content.id and publication.published_at is not None and publication.sku == "BR100"
        untouched = (await s.execute(select(Asset).where(Asset.batch_id == recent["batch_id"]))).scalars().all()
        assert all(a.files_removed_at is None and a.processed_key for a in untouched)

    # A second run finds nothing more to do.
    async with world["sm"]() as s:
        again = await upload_retention.run(s, storage, now=NOW)
    assert (again.groups, again.files, again.freed_bytes) == (0, 0, 0)


async def test_a_group_nobody_published_is_kept_30_days_from_when_it_was_last_worked_on(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    abandoned = await _listing(world, uploaded=31 * DAY, group="A")
    fresh = await _listing(world, uploaded=29 * DAY, group="B")
    written_lately = await _listing(world, uploaded=40 * DAY, written=5 * DAY, group="C")
    drafted_long_ago = await _listing(world, uploaded=45 * DAY, written=45 * DAY, drafted=31 * DAY, group="D")
    drafted_lately = await _listing(world, uploaded=45 * DAY, written=45 * DAY, drafted=10 * DAY, group="E")
    async with world["sm"]() as s:
        result = await upload_retention.run(s, storage, now=NOW)

    assert (result.published_groups, result.unpublished_groups) == (0, 2)
    assert _left(storage, abandoned) == [] and _left(storage, drafted_long_ago) == []
    for kept in (fresh, written_lately, drafted_lately):
        assert len(_left(storage, kept)) == 6
    async with world["sm"]() as s:
        # A draft that exists on Etsy is still on record and can still be published.
        publication = await s.get(ListingPublication, drafted_long_ago["publication_id"])
        assert publication.state == "draft" and publication.content_id == drafted_long_ago["content_id"]
        # With no listing text, the first image in the seller's order is the cover.
        first = await s.get(Asset, abandoned["assets"][0])
        assert first.thumbnail_key is not None and storage.exists(first.thumbnail_key)


async def test_each_group_of_a_batch_is_judged_on_its_own(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    published = await _listing(world, uploaded=16 * DAY, written=16 * DAY, drafted=16 * DAY, published=15 * DAY, group="A")
    waiting = await _listing(world, uploaded=16 * DAY, written=16 * DAY, group="B", batch_id=published["batch_id"])
    async with world["sm"]() as s:
        result = await upload_retention.run(s, storage, now=NOW)
    assert (result.published_groups, result.unpublished_groups) == (1, 0)
    assert _left(storage, published) == [] and len(_left(storage, waiting)) == 6


async def test_a_dry_run_counts_the_same_and_deletes_nothing(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=15 * DAY)
    async with world["sm"]() as s:
        dry = await upload_retention.run(s, storage, now=NOW, apply=False)
    assert (dry.applied, dry.published_groups, dry.files, dry.thumbnails) == (False, 1, 9, 0)
    assert len(_left(storage, old)) == 6
    async with world["sm"]() as s:
        assert all(a.files_removed_at is None for a in (await s.execute(select(Asset).where(Asset.batch_id == old["batch_id"]))).scalars())
        real = await upload_retention.run(s, storage, now=NOW)
    assert (real.files, real.freed_bytes) == (dry.files, dry.freed_bytes) and _left(storage, old) == []


async def test_a_batch_with_work_still_running_waits_for_the_next_run(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=15 * DAY)
    bob = world["bob"]
    async with world["sm"]() as s:
        # "Replace images" names its batch in the payload; a draft job on the row.
        job = Job(tenant_id=bob.tenant_id, connection_id=bob.connection_id, type="replace_images", status=JobStatus.queued,
                  payload={"batch_id": str(old["batch_id"]), "listing_id": 1})
        s.add(job)
        await s.commit()
        result = await upload_retention.run(s, storage, now=NOW)
        assert (result.groups, result.waiting) == (0, 1) and len(_left(storage, old)) == 6
        job.status = JobStatus.succeeded
        await s.commit()
        result = await upload_retention.run(s, storage, now=NOW)
    assert (result.groups, result.waiting) == (1, 0) and _left(storage, old) == []


async def test_a_run_that_was_cut_off_is_finished_by_the_next(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=15 * DAY)
    # The first run wrote the thumbnail and deleted the files, then died before marking the rows.
    kept = f"{world['bob'].tenant_id}/{old['batch_id']}/kept/{old['assets'][0]}.jpg"
    storage.put(kept, b"thumbnail")
    for original, processed in old["keys"]:
        storage.delete_with_derivatives(original)
        storage.delete_with_derivatives(processed)
    async with world["sm"]() as s:
        result = await upload_retention.run(s, storage, now=NOW)
        cover = await s.get(Asset, old["assets"][0])
    assert (result.groups, result.files, result.thumbnails) == (1, 0, 1)
    assert cover.files_removed_at is not None and cover.thumbnail_key == kept and storage.get(kept) == b"thumbnail"


async def test_the_days_are_the_admins_to_set_and_the_change_is_audited(world) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    week_old = await _listing(world, uploaded=9 * DAY, written=9 * DAY, drafted=9 * DAY, published=8 * DAY)
    a, b = world["a"], world["b"]
    assert (await b.put("/api/admin/upload-retention", json={"published_days": 7, "unpublished_days": 30})).status_code == 404
    assert (await b.get("/api/admin/disk")).status_code == 404
    assert (await world["anon"].get("/api/admin/disk")).status_code in (401, 404)
    for bad in ({"published_days": 0, "unpublished_days": 30}, {"published_days": 7}, {"published_days": 7, "unpublished_days": 99999}):
        assert (await a.put("/api/admin/upload-retention", json=bad)).status_code == 422

    seen = (await a.get("/api/admin/disk")).json()
    assert seen["retention"] == seen["retention_defaults"] == {"published_days": 14, "unpublished_days": 30}
    async with world["sm"]() as s:
        assert (await upload_retention.run(s, storage, now=NOW)).groups == 0

    changed = await a.put("/api/admin/upload-retention", json={"published_days": 7, "unpublished_days": 21})
    assert changed.status_code == 200 and changed.json() == {"published_days": 7, "unpublished_days": 21}
    seen = (await a.get("/api/admin/disk")).json()
    assert seen["retention"] == {"published_days": 7, "unpublished_days": 21} and seen["retention_defaults"]["published_days"] == 14
    async with world["sm"]() as s:
        entry = (await s.execute(select(AuditLog).where(AuditLog.action == "app.upload_retention_changed"))).scalars().one()
        assert entry.details["previous"] == {"published_days": 14, "unpublished_days": 30} and entry.details["new"]["published_days"] == 7
        result = await upload_retention.run(s, storage, now=NOW)
    assert (result.published_groups, result.published_days) == (1, 7) and _left(storage, week_old) == []


async def test_after_the_files_are_gone_the_app_shows_the_cover_and_refuses_what_needs_them(world, monkeypatch) -> None:  # noqa: F811
    async def no_text_problem(session, content):
        return None  # this test is about the files, not the listing's wording

    monkeypatch.setattr(publish, "_content_problem", no_text_problem)
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=15 * DAY)
    async with world["sm"]() as s:
        await upload_retention.run(s, storage, now=NOW)
    b = world["b"]
    cover, second = old["assets"][0], old["assets"][1]

    detail = (await b.get(f"/api/batches/{old['batch_id']}")).json()
    flags = {a["id"]: (a["files_removed"], a["has_thumbnail"]) for a in detail["assets"]}
    assert flags[str(cover)] == (True, True) and flags[str(second)] == (True, False)

    # The cover is still shown, at whatever size a list asks for; the others are gone.
    for query in ("", "?w=224", "?w=896", "?w=448&ar=4:5"):
        shown = await b.get(f"/api/assets/{cover}/image{query}")
        assert shown.status_code == 200 and shown.headers["content-type"] == "image/jpeg", query
        assert Image.open(io.BytesIO(shown.content)).width <= upload_retention.THUMBNAIL_WIDTH
    assert (await b.get(f"/api/assets/{second}/image?w=224")).status_code == 410
    assert (await world["a"].get(f"/api/assets/{cover}/image")).status_code == 404  # still only its owner's

    crop = await b.put(f"/api/assets/{cover}/cover-crop", json={"x": 0, "y": 0, "size": 900})
    assert crop.status_code == 409 and crop.json()["detail"] == upload_retention.REMOVED
    assert (await b.delete(f"/api/assets/{second}")).status_code == 409
    order = await b.put(f"/api/batches/{old['batch_id']}/groups/order", json={"group_key": "BR100", "asset_ids": [str(i) for i in reversed(old["assets"])]})
    assert order.status_code == 409
    replace = await b.post("/api/shop/listings/2002/replace-images", json={"batch_id": str(old["batch_id"]), "group_key": "BR100"})
    assert replace.status_code == 409 and replace.json()["detail"] == upload_retention.REMOVED

    # Its draft exists in the shop it was published in; no other draft can be made from it.
    async with world["sm"]() as s:
        publication = await s.get(ListingPublication, old["publication_id"])
        await s.delete(publication)
        await s.commit()
    planned = (await b.post(f"/api/batches/{old['batch_id']}/publish", json={})).json()
    assert planned["jobs"] == [] and planned["skipped"][0]["reason"] == publish.FILES_REMOVED
    matrix = (await b.post(f"/api/batches/{old['batch_id']}/publish/preview", json={})).json()
    assert [(c["state"], c["reason"]) for c in matrix["rows"][0]["cells"]] == [("unavailable", publish.FILES_REMOVED)]

    # Deleting the batch still works, and takes the kept thumbnail with it.
    assert (await b.delete(f"/api/batches/{old['batch_id']}")).status_code in (200, 204)
    assert not storage.exists(f"{world['bob'].tenant_id}/{old['batch_id']}/kept/{cover}.jpg")


def test_storage_says_what_a_delete_frees_and_what_is_stored(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put("t/b/original/a.png", b"x" * 1000)
    storage.put("t/b/processed/a.jpg", b"x" * 400)
    storage.put("t/b/processed/a.jpg.v3.w224.jpg", b"x" * 50)
    storage.put("t/b/processed/a.jpg.v3.w448-4x5.jpg", b"x" * 70)
    storage.put("t/b/processed/ab.jpg", b"x" * 9)  # another image, not a preview of a.jpg
    storage.put("t/b/kept/a.jpg", b"x" * 30)
    assert storage.measure_with_derivatives("t/b/processed/a.jpg") == (3, 520)
    assert storage.measure_with_derivatives("t/b/processed/missing.jpg") == (0, 0)
    assert storage.usage() == {"uploads_bytes": 1000, "uploads_files": 1, "derivatives_bytes": 559, "derivatives_files": 5}
    assert storage.delete_with_derivatives("t/b/processed/a.jpg") == 520
    assert storage.delete_with_derivatives("t/b/processed/a.jpg") == 0
    assert storage.exists("t/b/processed/ab.jpg") and storage.exists("t/b/original/a.png")
    assert LocalStorage(tmp_path / "nothing-yet").usage()["uploads_files"] == 0
    with pytest.raises(ValueError):
        storage.measure_with_derivatives("../outside")


async def test_disk_usage_by_category_says_unknown_until_the_host_reports(world) -> None:  # noqa: F811
    await _settle(world)
    storage, redis, a = _storage(world), world["redis"], world["a"]
    await _listing(world, uploaded=1 * DAY)
    seen = (await a.get("/api/admin/disk")).json()
    by = {c["key"]: c for c in seen["categories"]}
    assert list(by) == ["uploads", "derivatives", "database", "backups", "docker", "other"]
    assert by["uploads"]["bytes"] == 3 * len(IMAGE) and by["uploads"]["files"] == 3
    # Processed copies and previews (and the seed's two processed files).
    assert by["derivatives"]["bytes"] >= 3 * (len(IMAGE) + 500) and by["derivatives"]["files"] == 8
    # Nothing from the host yet: unknown, never zero.
    assert by["backups"]["bytes"] is None and by["docker"]["bytes"] is None and by["other"]["bytes"] is None
    assert seen["host_reported_at"] is None and seen["host_fresh"] is False
    assert seen["total_bytes"] > seen["free_bytes"] > 0 and seen["last_run"] is None
    assert by["backups"]["label"] == "Backups" and "sellers" in by["uploads"]["note"]

    # deploy/disk-check.sh leaves its figures in Redis.
    await redis.set(disk.HOST_KEY, json.dumps({"at": time.time(), "backups_bytes": 3_000_000_000, "docker_bytes": 7_500_000_000}))
    seen = (await a.get("/api/admin/disk")).json()
    by = {c["key"]: c["bytes"] for c in seen["categories"]}
    assert (by["backups"], by["docker"], seen["host_fresh"]) == (3_000_000_000, 7_500_000_000, True)
    assert seen["host_reported_at"] is not None

    # A report from hours ago is still shown, and said to be old.
    await redis.set(disk.HOST_KEY, json.dumps({"at": time.time() - 5 * 3600, "backups_bytes": 1, "docker_bytes": 2}))
    assert (await a.get("/api/admin/disk")).json()["host_fresh"] is False

    # The storage walk is not repeated on every page view.
    storage.put("x/y/original/new.png", b"z" * 4096)
    by = {c["key"]: c["bytes"] for c in (await a.get("/api/admin/disk")).json()["categories"]}
    assert by["uploads"] == 3 * len(IMAGE)
    async with world["sm"]() as s:
        fresh = await disk.snapshot(s, storage, redis, fresh=True)
    assert fresh.get("uploads") == 3 * len(IMAGE) + 4096
    assert disk.size(None) == "unknown" and disk.size(616 * 1024**2) == "616 MB" and disk.size(int(6.14 * 1024**3)) == "6.1 GB"


async def test_the_daily_job_runs_once_a_day_and_reports_what_it_freed(world, tmp_path, test_settings, monkeypatch) -> None:  # noqa: F811
    await _settle(world)
    storage = _storage(world)
    old = await _listing(world, uploaded=20 * DAY, written=20 * DAY, drafted=20 * DAY, published=20 * DAY)
    abandoned = await _listing(world, uploaded=400 * DAY, group="Z")
    sent: list[str] = []

    async def record(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", record)
    ctx = {"redis": world["redis"], "sessionmaker": world["sm"], "storage": storage}

    # Development: the job only reports.
    set_settings_override(test_settings.model_copy(update={"storage_dir": str(tmp_path), "upload_retention_apply": False}))
    dry = await upkeep.daily_upkeep(ctx)
    assert dry["applied"] is False and dry["published_groups"] == 1 and len(_left(storage, old)) == 6
    assert "would free (dry run, nothing was deleted)" in sent[-1]
    assert await upkeep.daily_upkeep(ctx) is None and len(sent) == 1  # once a day

    await world["redis"].delete(*await world["redis"].keys("upkeep:done:*"))  # the next day
    set_settings_override(test_settings.model_copy(update={"storage_dir": str(tmp_path), "upload_retention_apply": True}))
    done = await upkeep.daily_upkeep(ctx)
    assert done["applied"] is True and (done["published_groups"], done["unpublished_groups"], done["files"]) == (1, 1, 18)
    assert _left(storage, old) == [] and _left(storage, abandoned) == []
    text = sent[-1]
    assert text.startswith("Listyro daily summary, ")
    assert "Upload cleanup freed" in text and "18 files of 2 listings (1 published 14+ days ago, 1 not published after 30 days)" in text
    assert "free of" in text and "uploads " in text and "backups unknown" in text and "disk-check.sh" in text
    # Counts and sizes only.
    for private in ("bob@example.com", "BR100", VALID_TITLE, str(world["bob"].tenant_id)):
        assert private not in text

    # The last run is kept for the admin panel.
    async with world["sm"]() as s:
        assert (await s.get(AppSetting, upload_retention.LAST_KEY)).value["freed_bytes"] == done["freed_bytes"]
    last = (await world["a"].get("/api/admin/disk")).json()["last_run"]
    assert (last["applied"], last["files"], last["published_groups"], last["thumbnails"]) == (True, 18, 1, 2)

    # A day with nothing due still sends its summary.
    await world["redis"].delete(*await world["redis"].keys("upkeep:done:*"))  # the next day
    await upkeep.daily_upkeep(ctx)
    assert "Upload cleanup: nothing was due." in sent[-1] and len(sent) == 3


async def test_a_failed_cleanup_says_so_and_can_run_again_the_same_day(world, monkeypatch) -> None:  # noqa: F811
    sent: list[str] = []

    async def record(text: str) -> bool:
        sent.append(text)
        return True

    async def broken(*args, **kwargs):
        raise RuntimeError("disk error")

    monkeypatch.setattr(alerts, "send", record)
    monkeypatch.setattr(upload_retention, "run", broken)
    ctx = {"redis": world["redis"], "sessionmaker": world["sm"]}
    with pytest.raises(RuntimeError):
        await upkeep.daily_upkeep(ctx)
    assert "the upload cleanup failed" in sent[-1]
    assert await world["redis"].keys("upkeep:done:*") == []
