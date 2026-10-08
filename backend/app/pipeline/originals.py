"""One-off: delete the original uploads already stored (``storage-originals``).

New uploads keep only their processed copy (pipeline/ingest.py); every later
step reads that copy. This removes the originals stored before, and clears
``asset.storage_key`` so nothing points at them.

Per asset with a stored original key:
  * processed, and its processed copy is on disk: the original (and any preview
    cached beside it) is deleted
  * processing failed: nothing can use the original; it is deleted
  * files already removed by upload retention: no file; the key is cleared
  * processed but its processed copy is **missing**: left alone and reported
    (deleting the original would leave nothing)

Dry run by default: counts what would go. ``apply=True`` deletes, file first and
then the row, committing every :data:`CHUNK` assets, so a run that is cut off is
finished by the next one.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asset, AssetStatus
from app.pipeline.storage import Storage

CHUNK = 200


@dataclass
class Result:
    applied: bool
    assets: int = 0  # assets whose original goes (or went)
    files: int = 0
    freed_bytes: int = 0
    cleared_only: int = 0  # keys cleared with no file left to delete
    kept_no_processed: int = 0  # processed copy missing: original kept

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


async def run(session: AsyncSession, storage: Storage, *, apply: bool = False) -> Result:
    result = Result(applied=apply)
    rows = (await session.execute(
        select(Asset.id, Asset.storage_key, Asset.processed_key, Asset.status, Asset.files_removed_at)
        .where(Asset.storage_key.is_not(None))
        .order_by(Asset.id)
    )).all()
    cleared: list = []
    for asset_id, original, processed, status, removed in rows:
        files, size = await asyncio.to_thread(storage.measure_with_derivatives, original)
        if removed is None and status is AssetStatus.processed:
            if processed is None or not await asyncio.to_thread(storage.exists, processed):
                result.kept_no_processed += 1
                continue
        if files == 0:
            result.cleared_only += 1
        else:
            result.assets += 1
            result.files += files
            result.freed_bytes += size
        if apply:
            if files:
                await asyncio.to_thread(storage.delete_with_derivatives, original)
            cleared.append(asset_id)
            if len(cleared) >= CHUNK:
                await _clear(session, cleared)
    if apply and cleared:
        await _clear(session, cleared)
    return result


async def _clear(session: AsyncSession, ids: list) -> None:
    await session.execute(update(Asset).where(Asset.id.in_(ids)).values(storage_key=None))
    await session.commit()
    ids.clear()
