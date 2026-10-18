"""``python -m app.cli mockup-occlusion [--apply] [--email <address>]``: what is in front of
the design (hair, arms), worked out again with the occlusion guards (pipeline/occlusion.py)
for photos prepared before them.

Every ready photo is checked: the segmenter runs again on the stored blank (on our own CPU;
no network, no external service, no Etsy or AI call) and the new map is compared with the one
the photo has. **Photos still waiting for the seller's check** take the new map and each
area's switch (off, with the reason, where more than a quarter would be hidden or the design
would be cut into pieces), with ``--apply``; the seller's "Keep in front" brush is kept on top.
**Confirmed photos are only listed**, with how much would change, and never modified: the
seller looked at them and said they look right. A dry run unless ``--apply``. About 1-2 s per
photo; run it in the worker container (it needs the image analysis models).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, Tenant
from app.pipeline.storage import Storage


def _png(plane: Any) -> bytes:
    import numpy as np
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.clip(plane * 255.0, 0, 255).astype(np.uint8), "L").save(buf, format="PNG")
    return buf.getvalue()


def recheck(blank: bytes, width: int, height: int, runner: Any) -> Any:
    """The guarded "in front" map for a stored blank, at the photo's size (float 0..1)."""
    import numpy as np
    from PIL import Image

    from app.pipeline import garments, occlusion

    im = Image.open(io.BytesIO(blank)).convert("RGB")
    small = im.copy()
    small.thumbnail((garments.WORK, garments.WORK))
    rgb = np.asarray(small)
    probs = runner.segment(rgb)
    person = garments.is_person(rgb, probs, runner)
    front = occlusion.person_front(probs, person)
    full = Image.fromarray(np.clip(front * 255.0, 0, 255).astype(np.uint8), "L").resize((width, height), Image.BILINEAR)
    return np.asarray(full, dtype=np.float32) / 255.0


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None,
              runner: Any | None = None) -> str:
    import numpy as np

    from app.pipeline import mockup_vision as vision
    from app.pipeline import occlusion
    from app.workers.mockups import _plane, guard_rows, photo_area_rows, save_front

    runner = runner if runner is not None else vision.default_runner()
    if runner is None:
        return "The image analysis models are not installed here: run this in the worker container."
    async with sm() as session:
        query = select(MockupPhoto).where(MockupPhoto.status == "ready", MockupPhoto.image_key.is_not(None))
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupPhoto.tenant_id == tenant.id)
        photos = list((await session.execute(query.order_by(MockupPhoto.created_at))).scalars())
        waiting_changed = waiting_same = applied = failed = 0
        confirmed_lines: list[str] = []
        for photo in photos:
            w, h = photo.width or 1, photo.height or 1
            try:
                blank = storage.get(photo.image_key or "")
                new = await asyncio.to_thread(recheck, blank, w, h, runner)
            except Exception:  # noqa: BLE001 - one photo that cannot be read does not stop the rest
                failed += 1
                continue
            rows = photo_area_rows(photo)
            if not rows or not rows[0]:
                continue
            new_rows, cleaned, _ = guard_rows(_png(new), rows, w, h)
            new_map = _plane(cleaned, w, h) if cleaned else new
            old_key = photo.occluder_auto_key or (photo.occluder_key if not photo.occluder_manual_key else None)
            old_map = _plane(storage.get(old_key), w, h) if old_key else np.zeros((h, w), np.float32)
            inside = np.zeros((h, w), bool)
            for r in rows:
                inside |= occlusion.area_mask(r["print_area"], h, w)
            differs = float(((new_map > occlusion.ON) != (old_map > occlusion.ON))[inside].mean()) if inside.any() else 0.0
            switches = sum((a.get("occlusion") is not False) != b["occlusion"] for a, b in zip(rows, new_rows))
            if photo.confirmed_at is not None:
                if differs > 0.001 or switches:
                    confirmed_lines.append(f"  {photo.name} ({photo.id}): {differs:.1%} of its print areas and "
                                           f"{switches} area switch(es) would change; not modified (confirmed)")
                continue
            if differs <= 0.001 and not switches:
                waiting_same += 1
                continue
            waiting_changed += 1
            if apply:
                save_front(storage, photo, auto=_png(new_map) if new_map.max() > 0 else None)
                merged = [dict(g) for g in photo.garments or []]
                for g, b in zip([g for g in merged if g.get("print_area")], new_rows):
                    g["occlusion"], g["occlusion_note"], g["occlusion_hidden"] = b["occlusion"], b.get("occlusion_note"), b.get("occlusion_hidden")
                photo.garments = merged
                photo.version += 1
                applied += 1
        if apply:
            await session.commit()
    lines = [
        f"{len(photos)} photo(s) checked with the occlusion guards ({failed} could not be read).",
        f"Waiting for the seller's check: {waiting_changed} would change, {waiting_same} unchanged"
        + (f"; {applied} updated." if apply else "; run with --apply to update them."),
        f"Confirmed (listed only, never modified): {len(confirmed_lines)} would change.",
        *confirmed_lines,
    ]
    return "\n".join(lines)
