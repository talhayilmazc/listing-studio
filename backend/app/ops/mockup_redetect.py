"""``python -m app.cli mockup-redetect [--apply] [--email <address>]``: the print-area suggestions
of existing template photos worked out again with the current garment geometry
(pipeline/garments.py, pipeline/print_rule.py), without losing anything the seller did.

For every ready photo the analysis runs again on the stored blank, on our own CPU (no
network, no external service, no Etsy or AI call), and only the suggestions change:

* a photo still waiting for the seller's check whose areas are all as suggested
  (``source`` "default") gets the new suggestions as its areas, garments added or
  removed with them; it still waits for "Looks right";
* every other photo (confirmed, or moved, tapped or copied by the seller) keeps its print
  areas, its confirmation and its order; when the analysis finds the same number of
  garments, each area's suggestion (``detected_area``: "Reset to suggestion", the guides)
  and the geometry it was built from are updated, so the seller's confirmed placement
  can be learnt from (pipeline/placement.py).

Confirmed photos go first, and each account's own placement is learnt from them before
its waiting photos are suggested again. Occluders, brushes, sizes and names are not touched
(``mockup-occluders`` redoes the occluder). A dry run unless ``--apply``. About 4-5 s per
photo; run it in the worker container, where the models are loaded once.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, Tenant
from app.pipeline import mockups, placement
from app.pipeline.storage import Storage

SECONDS_PER_PHOTO = 4.5


def _waiting_as_suggested(photo: MockupPhoto) -> bool:
    rows = photo.garments or []
    return photo.confirmed_at is None and all((r.get("source") or "default") == "default" for r in rows)


def apply_found(photo: MockupPhoto, found: list[dict[str, Any]], confidence: float | None,
                several: bool | str = False) -> str:
    """The new suggestions on one photo; what happened ("replaced", "suggestions", "kept").
    Every garment found is kept as a candidate; the photo's suggestions are the main
    garment's unless its template has several garments in one photo (``several``)."""
    if not found:
        return "kept"
    candidates = [{**{k: v for k, v in g.items() if k != "notes"}, "source": "default",
                   "detected_area": g["print_area"]} for g in found]
    found = mockups.chosen_rows(candidates, several)
    analysis = dict(getattr(photo, "analysis", None) or {})
    analysis["candidates"] = candidates
    photo.analysis = analysis
    if _waiting_as_suggested(photo):
        rows = [dict(g) for g in found]
        photo.garments = rows
        photo.print_area, photo.print_area_source = rows[0]["print_area"], "default"
        photo.print_area_confidence = confidence
        photo.version += 1
        return "replaced"
    rows = [dict(g) for g in (photo.garments or [])]
    if len(rows) != len(found):
        return "kept"  # which suggestion belongs to which area is not known: nothing changes
    for row, g in zip(rows, found, strict=True):
        row["detected_area"] = g["print_area"]
        for k in ("geometry", "ratios", "personal_n", "guards", "layout", "main"):
            if k in g:
                row[k] = g[k]
    photo.garments = rows
    photo.version += 1
    return "suggestions"


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        query = select(MockupPhoto).where(MockupPhoto.status == "ready", MockupPhoto.image_key.is_not(None))
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupPhoto.tenant_id == tenant.id)
        photos = list((await session.execute(query.order_by(MockupPhoto.created_at))).scalars())
        waiting = sum(_waiting_as_suggested(p) for p in photos)
        if not apply:
            return (f"{len(photos)} template photo(s) would be analysed again (about "
                    f"{len(photos) * SECONDS_PER_PHOTO / 60:.1f} min): {waiting} waiting for a check with suggested "
                    f"areas would get the new suggestions; {len(photos) - waiting} keep their areas and confirmations. "
                    "Run with --apply.")
        # Confirmed (and seller-edited) photos first: their placement is learnt before the
        # waiting photos of the same account are suggested again.
        photos.sort(key=lambda p: (_waiting_as_suggested(p), p.created_at))
        counts = {"replaced": 0, "suggestions": 0, "kept": 0, "failed": 0}
        tables: dict[uuid.UUID, dict[str, Any]] = {}
        started = time.monotonic()
        for photo in photos:
            personal = None
            if _waiting_as_suggested(photo):
                if photo.tenant_id not in tables:
                    tables[photo.tenant_id] = await placement.personal(session, photo.tenant_id)
                personal = tables[photo.tenant_id]
            try:
                blank = storage.get(photo.image_key or "")
                prepared = await asyncio.to_thread(mockups.prepare_blank, blank, "default", "default", photo.name, personal)
            except Exception:  # noqa: BLE001 - one photo that cannot be read does not stop the rest
                counts["failed"] += 1
                continue
            what = apply_found(photo, prepared.garments, prepared.confidence, several=photo.layout)
            counts[what] += 1
            if what == "suggestions":
                await placement.record(session, photo)
            await session.commit()
        return (f"{len(photos) - counts['failed']} photo(s) analysed again in {time.monotonic() - started:.0f} s: "
                f"{counts['replaced']} got new suggested areas (still waiting for a check), "
                f"{counts['suggestions']} kept their areas with updated suggestions, "
                f"{counts['kept']} unchanged (a different number of garments found)"
                + (f"; {counts['failed']} could not be read and were left as they were" if counts["failed"] else "") + ".")
