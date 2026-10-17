"""``python -m app.cli mockup-layouts [--apply] [--email <address>]``: each template photo's
layout (single garment, front & back, several garments) and its suggested areas, worked out
again for photos prepared before photos had layouts.

Only photos still waiting for the seller's check whose areas are all as suggested
(``source`` "default") change: the analysis runs again on the stored blank, on our own CPU
(no network, no external service, no Etsy or AI call); the photo's layout becomes the
analysis's when it is sure (one the seller chose stays), and its areas follow it (the front
garment's front area and the back garment's back area for front & back; every garment's for
several). Confirmed photos, and photos whose areas the seller moved, added or copied, are not
touched. A dry run unless ``--apply``. About 4-5 s per photo; run it in the worker container.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, Tenant
from app.pipeline import mockups, placement
from app.pipeline.storage import Storage

SECONDS_PER_PHOTO = 4.5


def waiting_as_suggested(photo: MockupPhoto) -> bool:
    return photo.confirmed_at is None and all((r.get("source") or "default") == "default" for r in (photo.garments or []))


def apply_layout(photo: MockupPhoto, prepared: mockups.PreparedBlank) -> bool:
    """The analysis's layout and suggestions on a waiting photo; False when nothing changed."""
    found = prepared.garments or [{"print_area": prepared.print_area, "confidence": prepared.confidence}]
    candidates = [{**{k: v for k, v in g.items() if k != "notes"}, "source": "default", "detected_area": g["print_area"]}
                  for g in found]
    if photo.layout_source != "seller":
        photo.layout = prepared.layout if prepared.layout_confident else "single"
    chosen = mockups.chosen_rows(candidates, photo.layout)
    analysis = dict(photo.analysis or {})
    analysis["candidates"] = candidates
    analysis["layout_read"] = {"layout": prepared.layout, "confident": prepared.layout_confident}
    photo.analysis = analysis
    if [(r.get("print_area"), r.get("role")) for r in chosen] == [(r.get("print_area"), r.get("role")) for r in photo.garments or []]:
        return False
    photo.garments = chosen
    photo.print_area, photo.print_area_source = chosen[0]["print_area"], "default"
    photo.print_area_confidence = min((c.get("confidence") or 0.0) for c in chosen)
    photo.version += 1
    return True


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        query = select(MockupPhoto).where(MockupPhoto.status == "ready", MockupPhoto.image_key.is_not(None),
                                          MockupPhoto.confirmed_at.is_(None))
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupPhoto.tenant_id == tenant.id)
        photos = [p for p in (await session.execute(query.order_by(MockupPhoto.created_at))).scalars() if waiting_as_suggested(p)]
        if not apply:
            return (f"{len(photos)} waiting photo(s) with suggested areas would get a layout and fresh suggestions (about "
                    f"{len(photos) * SECONDS_PER_PHOTO / 60:.1f} min); confirmed photos and areas the seller changed are "
                    "not touched. Run with --apply.")
        changed = failed = 0
        layouts: dict[str, int] = {}
        started = time.monotonic()
        for photo in photos:
            personal = await placement.personal(session, photo.tenant_id)
            try:
                blank = storage.get(photo.image_key or "")
                prepared = await asyncio.to_thread(mockups.prepare_blank, blank, "default", "default", photo.name, personal)
            except Exception:  # noqa: BLE001 - one photo that cannot be read does not stop the rest
                failed += 1
                continue
            changed += apply_layout(photo, prepared)
            layouts[photo.layout] = layouts.get(photo.layout, 0) + 1
            await session.commit()
        spread = ", ".join(f"{n} {k}" for k, n in sorted(layouts.items()))
        return (f"{len(photos) - failed} waiting photo(s) analysed in {time.monotonic() - started:.0f} s ({spread}); "
                f"{changed} got new suggested areas" + (f"; {failed} could not be read" if failed else "") + ".")
