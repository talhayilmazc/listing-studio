"""python -m app.cli storage-originals: the originals stored before go; a dry run first."""

from __future__ import annotations

from sqlalchemy import select, update

from app.cli import storage_originals
from app.db.models import Asset, AssetStatus
from app.pipeline import originals
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_upload_retention import DAY, IMAGE, _listing, _storage


async def test_a_dry_run_counts_and_apply_deletes_only_what_nothing_needs(world) -> None:  # noqa: F811
    storage = _storage(world)
    async with world["sm"]() as s:
        await originals.run(s, storage, apply=True)  # the fixture's own uploads, out of the way
    good = await _listing(world, uploaded=2 * DAY, images=2, group="OK")
    broken = await _listing(world, uploaded=2 * DAY, images=1, group="LOST")
    failed = await _listing(world, uploaded=2 * DAY, images=1, group="FAIL")
    # One image's processed copy is gone: its original must stay.
    storage.delete_with_derivatives(broken["keys"][0][1])
    async with world["sm"]() as s:
        await s.execute(update(Asset).where(Asset.id == failed["assets"][0]).values(status=AssetStatus.failed, processed_key=None))
        await s.commit()
        dry = await originals.run(s, storage)
    assert (dry.applied, dry.assets, dry.files, dry.freed_bytes, dry.kept_no_processed) == (False, 3, 3, 3 * len(IMAGE), 1)
    assert all(storage.exists(o) for o, _ in good["keys"])  # nothing deleted

    report = await storage_originals(world["sm"], apply=False, storage=storage)
    assert "would delete (dry run" in report and "3 images" in report and "processed copy is missing: 1" in report

    report = await storage_originals(world["sm"], apply=True, storage=storage)
    assert report.startswith("originals deleted: 3 images")
    assert not any(storage.exists(o) for o, _ in good["keys"]) and all(storage.exists(p) for _, p in good["keys"])
    assert storage.exists(broken["keys"][0][0])  # kept: it is all there is
    async with world["sm"]() as s:
        keys = {a.id: a.storage_key for a in (await s.execute(select(Asset))).scalars()}
    assert all(keys[i] is None for i in [*good["assets"], *failed["assets"]])
    assert keys[broken["assets"][0]] is not None
    # Run again: nothing left to do.
    async with world["sm"]() as s:
        again = await originals.run(s, storage, apply=True)
    assert (again.assets, again.kept_no_processed) == (0, 1)


async def test_keys_of_files_retention_already_removed_are_only_cleared(world) -> None:  # noqa: F811
    from datetime import UTC, datetime

    storage = _storage(world)
    async with world["sm"]() as s:
        await originals.run(s, storage, apply=True)  # the fixture's own uploads, out of the way
    gone = await _listing(world, uploaded=2 * DAY, images=1, group="GONE")
    storage.delete_with_derivatives(gone["keys"][0][0])
    async with world["sm"]() as s:
        await s.execute(update(Asset).where(Asset.id == gone["assets"][0]).values(files_removed_at=datetime.now(UTC), processed_key=None))
        await s.commit()
        result = await originals.run(s, storage, apply=True)
        asset = await s.get(Asset, gone["assets"][0])
    assert (result.assets, result.cleared_only) == (0, 1) and asset.storage_key is None
