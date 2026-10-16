"""``python -m app.cli mockup-occluders [--apply] [--email <address>]``: what is in front of
the garment, worked out again for template photos prepared before it was decided against
the fabric (pipeline/mockup_vision.py ``front_of``).

The old map summed the model's hair, skin, face and accessory probabilities: soft where
the coarse model hesitated, so a design enlarged into the hair showed through it, and
fabric read as half "accessory" faded the design. This runs the photo's analysis again,
on our own CPU, on the stored blank (no network, no external service, no Etsy or AI
call), and keeps only its occluder (and the foreground map "Add area" uses); the print
areas, the confirmation and the seller's "Keep in front" brush are kept as they are.

A dry run unless ``--apply`` (it says how many photos and about how long). About 4-5 s
per photo; run it in the worker container, where the models are loaded once.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, Tenant
from app.pipeline import mockups
from app.pipeline.storage import Storage

SECONDS_PER_PHOTO = 4.5


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        query = select(MockupPhoto).where(MockupPhoto.status == "ready", MockupPhoto.image_key.is_not(None))
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupPhoto.tenant_id == tenant.id)
        photos = list((await session.execute(query.order_by(MockupPhoto.created_at))).scalars())
        if not apply:
            return (f"{len(photos)} template photo(s) would be analysed again (about "
                    f"{len(photos) * SECONDS_PER_PHOTO / 60:.1f} min); print areas, confirmations and brushes are kept. "
                    "Run with --apply.")
        from app.workers.mockups import save_front

        done = failed = 0
        started = time.monotonic()
        for photo in photos:
            try:
                blank = storage.get(photo.image_key or "")
                prepared = await asyncio.to_thread(mockups.prepare_blank, blank, "default", "default", photo.name)
            except Exception:  # noqa: BLE001 - one photo that cannot be read does not stop the rest
                failed += 1
                continue
            await asyncio.to_thread(save_front, storage, photo, auto=prepared.occluder)
            if prepared.foreground is not None:
                key = f"{photo.tenant_id}/templates/{photo.id}/foreground.png"
                storage.put(key, prepared.foreground, "image/png")
                photo.foreground_key = key
            await session.commit()
            done += 1
        return (f"{done} photo(s) analysed again in {time.monotonic() - started:.0f} s"
                + (f"; {failed} could not be read and were left as they were" if failed else "") + ".")
