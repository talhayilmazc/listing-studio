"""What a batch is called.

A batch the seller named is called that. Otherwise its name is derived from
what is in it, so it can still be told apart: the first group's SKU and how
many more listings it holds ("BR5229 + 4 more"), or its short id when nothing
in it has a SKU. Computed here, once, so every screen that refers to a batch
(the Batches page, a schedule, a bulk action's summary) says the same thing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.grouping import UNSORTED
from app.db.models import Asset, UploadBatch

MAX_NAME_LENGTH = 80


def derived_name(batch_id: uuid.UUID, groups: dict[str, str | None]) -> str:
    """``groups``: each listing group's key and its SKU (None if it has none)."""
    skus = [sku for _, sku in sorted(groups.items()) if sku]
    if not skus:
        return f"Batch {str(batch_id)[:8]}"
    more = len(groups) - 1
    return f"{skus[0]} + {more} more" if more > 0 else skus[0]


def clean_name(raw: str | None) -> str | None:
    """A name as the seller typed it: trimmed, one line. Empty means "no name"."""
    name = " ".join((raw or "").split())
    return name[:MAX_NAME_LENGTH] or None


async def names_for(session: AsyncSession, batches: Iterable[UploadBatch]) -> dict[uuid.UUID, str]:
    """Each batch's name: the seller's, else the derived one."""
    batches = list(batches)
    out = {b.id: b.name for b in batches if b.name}
    unnamed = [b.id for b in batches if not b.name]
    if unnamed:
        groups: dict[uuid.UUID, dict[str, str | None]] = {i: {} for i in unnamed}
        rows = await session.execute(
            select(Asset.batch_id, Asset.group_key, Asset.parsed_sku).where(Asset.batch_id.in_(unnamed)).order_by(Asset.rank)
        )
        for batch_id, group_key, sku in rows.all():
            if group_key == UNSORTED:
                continue  # the Unsorted tray is not a listing
            key = group_key or ""
            groups[batch_id][key] = groups[batch_id].get(key) or sku
        for batch_id in unnamed:
            out[batch_id] = derived_name(batch_id, groups[batch_id])
    return out


async def names_by_id(session: AsyncSession, batch_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = list(set(batch_ids))
    if not ids:
        return {}
    rows = await session.execute(select(UploadBatch).where(UploadBatch.id.in_(ids)))
    return await names_for(session, rows.scalars())
