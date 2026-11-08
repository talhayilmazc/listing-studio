"""Example designs for a seller who has none ready (the first-run checklist's "Try the examples").

Three typographic designs of our own (scripts/example_designs.py: our words, our font, no brand,
character or famous phrase), each with two photos. Their listings are a real result: the app's own
vision and content steps wrote them once (scripts/example_listings.py) and the answer is saved in
``app/examples/examples.json``. Trying them:

* makes **no model call**: the listings come from that file;
* **counts for nothing**: no ``allowance_use`` row, no funnel step, no ``ai_call`` row;
* can **never reach Etsy**: the batch is ``is_example``, and every path that sends, schedules,
  rewrites or replaces refuses it (``refuse``; the draft and publish workers check again);
* is labelled "Example" wherever the batch or its listings are shown.

The seller can review, edit and approve the examples like any listing, to see how it works, and
delete the batch like any other. A second "Try the examples" opens the same batch again.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.scanner import rescan
from app.db.models import GeneratedContent, Tenant, UploadBatch
from app.pipeline.ingest import BatchIngestor
from app.pipeline.ingest import UploadFile as IngestFile

HERE = Path(__file__).resolve().parent.parent / "examples"
NAME = "Example designs"
#: What every refusal says (the API's 409, the workers' failure).
NOT_SENT = "Example listings can't be sent to Etsy. Upload a design of your own to make a listing you can send."
#: Stands in for the description of the seller's own listing, which a real profile copies.
SAMPLE_BODY = (
    "This is where your own description goes. When you pick one of your listings as a profile, "
    "Listyro copies its description, category, price and shipping into every new listing."
)
#: The folder (listing) names, in order.
TITLES = {"slow-mornings-club": "Slow mornings club", "tomato-season": "Tomato season", "small-kind-things": "Small kind things"}


@lru_cache
def saved() -> dict[str, Any]:
    """The saved result (scripts/example_listings.py)."""
    return json.loads((HERE / "examples.json").read_text(encoding="utf-8"))


def files() -> list[tuple[str, str, Path]]:
    """(slug, folder name, photo) for every example photo, in order: light first (the cover)."""
    out = []
    for listing in saved()["listings"]:
        slug = listing["slug"]
        for kind in ("light", "dark"):
            out.append((slug, TITLES[slug], HERE / f"{slug}-{kind}.jpg"))
    return out


async def is_example(session: AsyncSession, batch_id: uuid.UUID | None) -> bool:
    if batch_id is None:
        return False
    return bool(await session.scalar(select(UploadBatch.is_example).where(UploadBatch.id == batch_id)))


async def any_example(session: AsyncSession, batch_ids: list[uuid.UUID]) -> bool:
    if not batch_ids:
        return False
    return bool(await session.scalar(
        select(UploadBatch.id).where(UploadBatch.id.in_(list(set(batch_ids))), UploadBatch.is_example).limit(1)
    ))


async def refuse(session: AsyncSession, batch_ids: list[uuid.UUID] | uuid.UUID | None) -> None:
    """409 with ``NOT_SENT`` when any of these batches holds the examples."""
    ids = [batch_ids] if isinstance(batch_ids, uuid.UUID) else [b for b in (batch_ids or []) if b is not None]
    if await any_example(session, ids):
        raise HTTPException(status_code=409, detail=NOT_SENT)


async def existing(session: AsyncSession, tenant_id: uuid.UUID) -> UploadBatch | None:
    return await session.scalar(
        select(UploadBatch).where(UploadBatch.tenant_id == tenant_id, UploadBatch.is_example,
                                  UploadBatch.deleted_at.is_(None))
    )


async def create(session: AsyncSession, tenant: Tenant, ingestor: BatchIngestor) -> UploadBatch:
    """The account's example batch: made once from the bundled files and the saved result, or the
    one it already has. No model call, nothing counted."""
    batch = await existing(session, tenant.id)
    if batch is not None:
        return batch
    batch = await ingestor.create_batch(session, tenant.id)
    batch.is_example = True
    batch.name = NAME
    batch.grouping_mode = "folder"
    batch.send_to = None
    await session.commit()
    batch_id = batch.id
    for _slug, folder, path in files():
        await ingestor.add_file(session, batch_id, tenant.id, IngestFile(filename=path.name, data=path.read_bytes()),
                                group_key=folder, mode="folder")
    batch = await ingestor.finalize_batch(session, batch_id)
    from app.db.models import Asset

    assets = list((await session.execute(select(Asset).where(Asset.batch_id == batch_id))).scalars())
    by_folder: dict[str, list[Asset]] = {}
    for asset in assets:
        by_folder.setdefault(asset.group_key or "", []).append(asset)
    for listing in saved()["listings"]:
        members = by_folder.get(TITLES[listing["slug"]], [])
        if not members:
            continue
        cover = min(members, key=lambda a: (a.rank if a.rank is not None else 1_000_000, a.original_filename))
        attributes: dict[str, Any] = {"example": {"slug": listing["slug"]}, "search": {"opening": listing["opening"]},
                                      "vision": {"theme": listing.get("theme"), "themes": listing.get("themes", [])}}
        if listing.get("attributes"):
            attributes["listing"] = listing["attributes"]
        if listing.get("warnings"):
            attributes["title_warnings"] = list(listing["warnings"])
        content = GeneratedContent(
            tenant_id=tenant.id, batch_id=batch_id, asset_id=cover.id, written_from_asset_id=cover.id,
            title=listing["title"], tags=list(listing["tags"]),
            description=f"{listing['opening'].strip()}\n\n{SAMPLE_BODY}", attributes=attributes,
            source="ai", approved=False,
        )
        session.add(content)
        await session.flush()
        await rescan(session, content)
    await session.commit()
    await session.refresh(batch)
    return batch
