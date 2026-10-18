"""Mockups in the worker (pipeline/mockups.py): preparing template photos, making a
run's mockups, turning them into listings, and building ZIPs to download.

* **Preparing a photo**: finding the garment's outline and suggesting print areas
  takes seconds on a large photo, so it runs here; the photo shows "preparing" on the
  page until it is "ready" (or "failed", with the reason). The suggestion waits for
  the seller's "Looks right" before the photo is used.
* **A run** prints every design of the batch on every photo it holds (the confirmed
  photos of the chosen templates, front ones, back ones or both, in order). A photo
  with several garments gets the same design on each, or one design per garment in
  order. Each mockup is an output named "<SKU>_<template name>_<photo name>.jpg" (the
  design's file name when it has no SKU), unique in the run. Progress is saved after
  every mockup, per design.
* **Use for listings**: the chosen designs become listing groups (one per design,
  all its mockups as its photos, the cover the seller picked first). The photos
  read the outputs' files: nothing is copied.
* **Download**: a ZIP of the chosen designs (a folder per design, or flat), private
  to the account and deleted ``MockupZip.RETENTION_HOURS`` after it is ready.

No Etsy call and no model call is made here; nothing is counted against the
seller's "Listings generated" allowance.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage_cap
from app.db.models import (
    Asset,
    AssetStatus,
    MockupAdjustment,
    MockupDesign,
    MockupDesignVersion,
    MockupOutput,
    MockupPhoto,
    MockupRecolour,
    MockupRun,
    MockupSubgroup,
    MockupTemplate,
    MockupZip,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import mockup_vision as vision
from app.pipeline import mockups, placement, subgroups, upload_retention
from app.pipeline.images import ImageProcessingError
from app.pipeline.ingest import _rerank
from app.pipeline.storage import Storage

logger = logging.getLogger(__name__)

#: The most photos one run prints on (every design on each).
MAX_PHOTOS = 60
#: Said on the run when a design cannot be printed (its own words stay in the log).
DESIGN_FAILED = "Some designs could not be read as images."
#: Said on a photo the worker could not prepare.
PREPARE_FAILED = "This photo could not be prepared. Try another file (JPEG or PNG)."


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- template photos ---------------------------------------------------------------------------


async def prepare(session: AsyncSession, storage: Storage, photo_id: uuid.UUID) -> MockupPhoto | None:
    """Find the photo's outline and suggest its print areas; "ready", or "failed" with the reason."""
    photo = await session.get(MockupPhoto, photo_id)
    if photo is None or photo.status != "preparing" or not photo.source_key:
        return photo
    try:
        source = storage.get(photo.source_key)
        personal = await placement.personal(session, photo.tenant_id)  # the seller's own placement
        prepared = await asyncio.to_thread(mockups.prepare_blank, source, "default", "default", photo.name, personal)
    except (FileNotFoundError, ImageProcessingError, mockups.MockupError) as exc:
        logger.info("mockup photo %s could not be prepared: %s", photo.id, exc)
        photo.status, photo.error = "failed", PREPARE_FAILED
        await session.commit()
        return photo
    base = f"{photo.tenant_id}/templates/{photo.id}"
    storage.put(f"{base}/blank.jpg", prepared.image, "image/jpeg")
    storage.put(f"{base}/mask.png", prepared.mask, "image/png")
    if prepared.occluder is not None:
        storage.put(f"{base}/occluder_auto.png", prepared.occluder, "image/png")
        storage.put(f"{base}/occluder.png", prepared.occluder, "image/png")  # no brush yet: the same
    if prepared.foreground is not None:
        storage.put(f"{base}/foreground.png", prepared.foreground, "image/png")
    storage.delete_with_derivatives(photo.source_key)
    storage_cap.forget(photo.tenant_id)
    photo.image_key, photo.mask_key = f"{base}/blank.jpg", f"{base}/mask.png"
    photo.occluder_key = f"{base}/occluder.png" if prepared.occluder is not None else None
    photo.occluder_auto_key = f"{base}/occluder_auto.png" if prepared.occluder is not None else None
    photo.foreground_key = f"{base}/foreground.png" if prepared.foreground is not None else None
    photo.width, photo.height, photo.mask_source = prepared.width, prepared.height, prepared.mask_source
    photo.print_area_confidence = prepared.confidence
    analysis = {
        "method": prepared.method, "note": prepared.note, "detected_area": prepared.print_area,
        "details": prepared.details,
    }
    found = prepared.garments or [{"print_area": prepared.print_area, "confidence": prepared.confidence}]
    # Every garment found is kept as a candidate; the photo's areas follow its layout: the
    # seller's choice, else the analysis's when it is sure, else one garment.
    if photo.layout_source != "seller":
        photo.layout = prepared.layout if prepared.layout_confident else "single"
    candidates = [{**{k: v for k, v in g.items() if k != "notes"}, "source": "default",
                   "detected_area": g["print_area"]} for g in found]
    chosen = mockups.chosen_rows(candidates, photo.layout)
    analysis["candidates"] = candidates
    analysis["layout_read"] = {"layout": prepared.layout, "confident": prepared.layout_confident}
    if photo.layout == "several" and len(chosen) > 1:
        analysis["note"] = vision.NOTES["garments"].format(n=len(chosen))
    photo.analysis = analysis
    photo.print_area_confidence = min((c.get("confidence") or 0.0) for c in chosen) if chosen else prepared.confidence
    if photo.garments:  # areas copied from another photo while it was preparing are kept
        rows = [dict(g) for g in photo.garments]
        for i, g in enumerate(rows):
            if i < len(chosen):
                g["detected_area"] = chosen[i]["print_area"]
                for k in ("geometry", "ratios", "personal_n", "guards", "layout", "main", "role", "pair"):
                    if k in chosen[i]:
                        g[k] = chosen[i][k]
    else:
        rows = chosen
    rows, cleaned, notes = guard_rows(storage.get(photo.occluder_auto_key) if photo.occluder_auto_key else None,
                                      rows, photo.width or 1, photo.height or 1)
    if cleaned is not None:
        storage.put(f"{base}/occluder_auto.png", cleaned, "image/png")
        storage.put(f"{base}/occluder.png", cleaned, "image/png")  # no brush yet: the same
    if notes:
        analysis["note"] = notes[0]
        photo.analysis = analysis
    photo.garments = rows
    photo.print_area = rows[0]["print_area"]
    photo.print_area_source = "default" if all(r.get("source") == "default" for r in rows) else "seller"
    if prepared.view in ("front", "back"):
        photo.view = prepared.view
    photo.status, photo.error, photo.source_key = "ready", None, None
    photo.version += 1
    await session.commit()
    return photo


async def prepare_mockup_template(ctx: dict[str, Any], photo_id: str) -> str:
    """arq entrypoint (the job keeps its name: a photo queued before an update still runs)."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await prepare(session, get_storage(), uuid.UUID(photo_id))
        return done.status if done is not None else "missing"


_KEEP = object()


def save_front(storage: Storage, photo: MockupPhoto, *, auto: Any = _KEEP, brush: Any = _KEEP) -> None:
    """Write what is in front of the garment for ``photo``: the analysis's own map
    (``auto``: PNG bytes, None for none) and/or the seller's brush (``brush``), and the two
    combined into the occluder every mockup and preview reads. An argument left out keeps
    what the photo has. Photos prepared before migration 0069 kept the analysis's map only
    as ``occluder_key``: it is taken as the analysis's own the first time. The caller commits."""
    base = f"{photo.tenant_id}/templates/{photo.id}"
    if auto is _KEEP:
        if photo.occluder_auto_key:
            auto = storage.get(photo.occluder_auto_key)
        elif photo.occluder_key and not photo.occluder_manual_key:
            auto = storage.get(photo.occluder_key)  # before 0069: the occluder was the analysis's map
        else:
            auto = None
    if brush is _KEEP:
        brush = storage.get(photo.occluder_manual_key) if photo.occluder_manual_key else None
    if auto:
        storage.put(f"{base}/occluder_auto.png", auto, "image/png")
        photo.occluder_auto_key = f"{base}/occluder_auto.png"
    else:
        if photo.occluder_auto_key:
            storage.delete_with_derivatives(photo.occluder_auto_key)
        photo.occluder_auto_key = None
    if brush:
        storage.put(f"{base}/occluder_manual.png", brush, "image/png")
        photo.occluder_manual_key = f"{base}/occluder_manual.png"
    else:
        if photo.occluder_manual_key:
            storage.delete_with_derivatives(photo.occluder_manual_key)
        photo.occluder_manual_key = None
    combined = mockups.compose_occluder(auto, brush, photo.width or 1, photo.height or 1)
    if combined:
        storage.put(f"{base}/occluder.png", combined, "image/png")
        photo.occluder_key = f"{base}/occluder.png"
    else:
        if photo.occluder_key:
            storage.delete_with_derivatives(photo.occluder_key)
        photo.occluder_key = None
    photo.version += 1


def guard_rows(occluder_png: bytes | None, rows: list[dict], width: int, height: int
               ) -> tuple[list[dict], bytes | None, list[str]]:
    """Each area's "design goes behind hair and arms" switch from the occluder (pipeline/
    occlusion.py): blobs wholly inside an area are dropped from the map; an area that would be
    more than a quarter hidden, or cut into pieces, has its switch turned off and says why.
    Returns the rows, the cleaned map (PNG, or None when unchanged or absent) and the reasons."""
    import numpy as np
    from PIL import Image

    from app.pipeline import occlusion

    if not occluder_png:
        return [{**r, "occlusion": r.get("occlusion", True)} for r in rows], None, []
    occ = _plane(occluder_png, width, height)
    cleaned, checks = occlusion.guard(occ, [r.get("print_area") for r in rows])
    out, notes = [], []
    for r, c in zip(rows, checks):
        out.append({**r, "occlusion": c.occlusion, "occlusion_note": c.reason, "occlusion_hidden": c.hidden})
        if c.reason:
            notes.append(c.reason)
    changed = not np.array_equal(cleaned > occlusion.ON, occ > occlusion.ON)
    png = None
    if changed:
        buf = io.BytesIO()
        Image.fromarray(np.clip(cleaned * 255.0, 0, 255).astype(np.uint8), "L").save(buf, format="PNG")
        png = buf.getvalue()
    return out, png, notes


def photo_area_occlusion(photo: MockupPhoto) -> list[bool]:
    """Each area's switch, in ``photo_print_areas`` order (on unless turned off)."""
    return [r.get("occlusion") is not False for r in photo_area_rows(photo)]


def photo_print_areas(photo: MockupPhoto) -> list[list[list[float]]]:
    """A photo's print areas, one per garment, in the seller's order."""
    rows = [g.get("print_area") for g in (photo.garments or []) if g.get("print_area")]
    return rows or ([photo.print_area] if photo.print_area else [])


def photo_area_rows(photo: MockupPhoto) -> list[dict]:
    """The rows behind ``photo_print_areas``, in the same order (a photo without rows: one, empty)."""
    rows = [g for g in (photo.garments or []) if g.get("print_area")]
    return rows or ([{}] if photo.print_area else [])


def area_subgroup_names(photo: MockupPhoto, groups: list[MockupSubgroup],
                        tones: list[str | None] | None = None) -> list[str | None]:
    """Each area's subgroup name, in area order: the seller's link, the garment's tone, else
    the photo's subgroup (``groups``: the photo's template's subgroups, in order). ``tones``:
    each area's tone measured from the photo, for areas the analysis gave none (one the
    seller added where nothing was detected)."""
    own = next((g for g in groups if g.id == photo.subgroup_id), groups[0] if groups else None)
    rows = photo_area_rows(photo)
    if tones:
        rows = [r if r.get("tone") in ("light", "dark") or i >= len(tones) else {**r, "tone": tones[i]}
                for i, r in enumerate(rows)]
    return [getattr(subgroups.area_subgroup(r, own, groups), "name", None) for r in rows]


def measured_tones(blank: bytes, areas: list[list[list[float]]]) -> list[str | None]:
    """Each area's garment tone, read from the photo under it (the middle half of the area,
    CIELAB L*): what the analysis would have said for a garment it did not find."""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(blank)).convert("RGB")
    image.thumbnail((600, 600))
    rgb = np.asarray(image, dtype=np.float32) / 255
    h, w = rgb.shape[:2]
    out: list[str | None] = []
    for quad in areas:
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        cx, cy, hw, hh = sum(xs) / 4 * w, sum(ys) / 4 * h, (max(xs) - min(xs)) * w / 4, (max(ys) - min(ys)) * h / 4
        patch = rgb[max(0, int(cy - hh)):max(1, int(cy + hh)), max(0, int(cx - hw)):max(1, int(cx + hw))]
        if patch.size == 0:
            out.append(None)
            continue
        lin = np.where(patch <= 0.04045, patch / 12.92, ((patch + 0.055) / 1.055) ** 2.4)
        y = float(np.median(lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722))
        lightness = 116 * (y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116) - 16
        out.append(subgroups.tone_of(lightness))
    return out


def files_for_areas(design: MockupDesign, versions: list[MockupDesignVersion], names: list[str | None]) -> list[Any]:
    """The file each area of one photo prints for ``design``: its subgroup's file; when the
    design prints anywhere on the photo, an area whose subgroup has no file of it prints the
    design's own file (every area the seller set renders: on a photo of three white and three
    black tees a design made only for dark garments used to print on three areas of six).
    A design with nothing for any area of the photo: None for each (it does not print there)."""
    own = [subgroups.file_for(design, versions, n) for n in names]
    if any(f is not None for f in own):
        return [f if f is not None else design for f in own]
    return own


def order_groups(designs: list[Any], areas: int) -> list[list[Any]]:
    """"One design per garment, in order": the designs taken ``areas`` at a time, each
    mockup's list filled from the start of the list so every area prints (three designs on a
    six-area photo: a, b, c, a, b, c). The mockup belongs to its own designs (the first ones)."""
    if not designs or areas < 1:
        return []
    out = []
    for start in range(0, len(designs), areas):
        out.append([designs[(start + k) % len(designs)] for k in range(areas)])
    return out


async def template_subgroups(session: AsyncSession, template_ids: Any) -> dict[uuid.UUID, list[MockupSubgroup]]:
    ids = list({uuid.UUID(str(t)) for t in template_ids})
    out: dict[uuid.UUID, list[MockupSubgroup]] = {t: [] for t in ids}
    if ids:
        for g in (await session.execute(
            select(MockupSubgroup).where(MockupSubgroup.template_id.in_(ids))
            .order_by(MockupSubgroup.position, MockupSubgroup.created_at)
        )).scalars():
            out[g.template_id].append(g)
    return out


def prints_on(design: MockupDesign, versions: list[MockupDesignVersion], names: list[str | None]) -> bool:
    """Whether a design has a file for any area of a photo (a design made only for one subgroup
    prints only on that subgroup's photos)."""
    return any(subgroups.file_for(design, versions, n) is not None for n in names)


def photo_area_roles(photo: MockupPhoto) -> list[str]:
    """Each print area's role, "front" or "back", in the same order (a photo of a garment seen
    from behind: back)."""
    rows = [g for g in (photo.garments or []) if g.get("print_area")]
    if not rows:
        return ["back" if photo.view == "back" else "front"] if photo.print_area else []
    return [g.get("role") if g.get("role") in ("front", "back") else ("back" if photo.view == "back" else "front")
            for g in rows]


def design_for(role: str, front: Any, back: Any, fallback: str) -> Any:
    """The design image an area prints: a back area the design's back file, else the front
    design ("front") or nothing ("plain"), as its template says."""
    if role != "back":
        return front
    if back is not None:
        return back
    return front if fallback != "plain" else None


def _plane(data: bytes, width: int, height: int) -> Any:
    """A one-band PNG as float 0..1 at (height, width)."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("L")
    if im.size != (width, height):
        im = im.resize((width, height), Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


def find_at(blank: bytes, ground: bytes, occluder: bytes | None, tap: tuple[float, float],
            others: list[list[list[float]]], name: str = "",
            personal: dict[str, dict[str, float]] | None = None) -> tuple[list[list[float]], float, str] | None:
    """"Add area" (api/mockups.py): the garment under ``tap`` (shares of the photo) and
    its print area, as shares, with confidence and kind; None when nothing is there.
    ``ground`` is the photo's foreground map (or its garment mask for photos prepared
    before it was kept). In the API process, without a model: 1-2 s, about 300 MB."""
    import numpy as np
    from PIL import Image

    from app.pipeline import garments

    im = Image.open(io.BytesIO(blank)).convert("RGB")
    scale = min(1.0, garments.WORK / max(im.size))
    if scale < 1.0:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.BILINEAR)
    w, h = im.size
    rgb = np.asarray(im, dtype=np.uint8)
    fg = _plane(ground, w, h)
    occ = _plane(occluder, w, h) if occluder else None
    px = [[(x * w, y * h) for x, y in quad] for quad in others]
    found = garments.find_at(rgb, fg, occ, (tap[0] * w, tap[1] * h), px, name, personal)
    if found is not None:
        corners = [[round(min(max(x / w, 0.0), 1.0), 4), round(min(max(y / h, 0.0), 1.0), 4)] for x, y in found.corners]
        return corners, round(float(found.confidence), 3), found.kind
    # 2. Nothing found on the whole photo: the same search on a crop around the click, at
    # twice the detail (a small garment among others; no model runs, as above).
    full = Image.open(io.BytesIO(blank)).convert("RGB")
    fw, fh = full.size
    side = 0.5 * max(fw, fh)
    cx, cy = tap[0] * fw, tap[1] * fh
    box = (int(max(0, cx - side / 2)), int(max(0, cy - side / 2)), int(min(fw, cx + side / 2)), int(min(fh, cy + side / 2)))
    crop = full.crop(box)
    k = min(1.0, garments.WORK / max(crop.size))
    if k < 1.0:
        crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.BILINEAR)
    cw, ch = crop.size
    sx, sy = (box[2] - box[0]) / cw, (box[3] - box[1]) / ch
    fg_full = _plane(ground, fw, fh)[box[1]:box[3], box[0]:box[2]]
    fg_crop = np.asarray(Image.fromarray((fg_full * 255).astype(np.uint8)).resize((cw, ch), Image.BILINEAR), np.float32) / 255
    occ_crop = None
    if occluder:
        occ_full = _plane(occluder, fw, fh)[box[1]:box[3], box[0]:box[2]]
        occ_crop = np.asarray(Image.fromarray((occ_full * 255).astype(np.uint8)).resize((cw, ch), Image.BILINEAR), np.float32) / 255
    local = [[((x * fw - box[0]) / sx, (y * fh - box[1]) / sy) for x, y in quad] for quad in others]
    again = garments.find_at(np.asarray(crop, dtype=np.uint8), fg_crop, occ_crop, ((cx - box[0]) / sx, (cy - box[1]) / sy),
                             local, name, personal)
    if again is None:
        return None
    corners = [[round(min(max((box[0] + x * sx) / fw, 0.0), 1.0), 4), round(min(max((box[1] + y * sy) / fh, 0.0), 1.0), 4)]
               for x, y in again.corners]
    return corners, round(float(again.confidence) * 0.8, 3), again.kind


def candidate_at(photo: MockupPhoto, x: float, y: float, others: list[list[list[float]]]) -> dict | None:
    """"Click a garment to add an area", step 1: a garment the analysis found under the click
    that has no area yet (``analysis["candidates"]``): its torso, between its armpits from its
    collar down, holds the click. None when there is none."""
    import math

    w, h = photo.width or 1, photo.height or 1
    for c in (photo.analysis or {}).get("candidates") or []:
        geo = c.get("geometry") or {}
        q = c.get("print_area")
        if not q or not geo.get("armpits") or not geo.get("neck"):
            continue
        (lx, ly), (rx, ry) = [(p[0] * w, p[1] * h) for p in geo["armpits"]]
        chest = max(math.dist((lx, ly), (rx, ry)), 1.0)
        nx, ny = geo["neck"][0] * w, geo["neck"][1] * h
        px, py = x * w, y * h
        if not (min(lx, rx) <= px <= max(lx, rx) and ny - 0.1 * chest <= py <= ny + 1.6 * chest):
            continue
        cx, cy = sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4
        if any(_inside((cx, cy), o) for o in others):
            continue  # this garment has its area already
        return c
    return None


def _inside(p: tuple[float, float], q: list[list[float]]) -> bool:
    sign = None
    for i in range(4):
        (ax, ay), (bx, by) = q[i], q[(i + 1) % 4]
        c = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
        if c:
            if sign is None:
                sign = c > 0
            elif sign != (c > 0):
                return False
    return True


def like_at(x: float, y: float, like: list[list[float]], width: int, height: int) -> list[list[float]]:
    """Step 3: an area of ``like``'s size and angle centred where the seller clicked (shares)."""
    pts = [(p[0] * width, p[1] * height) for p in like]
    cx, cy = sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4
    dx, dy = x * width - cx, y * height - cy
    return [[round(min(max((px + dx) / width, 0.0), 1.0), 4), round(min(max((py + dy) / height, 0.0), 1.0), 4)]
            for px, py in pts]


def box_at(x: float, y: float, others: list[list[list[float]]], width: int, height: int) -> list[list[float]]:
    """A print area centred where the seller tapped, when no garment was found there: as
    wide as the other areas (their median), else a quarter of the photo, 1.2 x as tall
    as wide (a standard adult print), kept inside the photo. Shares of the photo."""
    import statistics

    widths = [abs(q[1][0] - q[0][0]) * width for q in others if len(q) == 4]
    side = statistics.median(widths) if widths else 0.25 * min(width, height)
    bw, bh = side / width, min(1.2 * side / height, 0.9)
    bw = min(bw, 0.9)
    cx = min(max(x, bw / 2), 1 - bw / 2)
    cy = min(max(y, bh / 2), 1 - bh / 2)
    x0, y0, x1, y1 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
    return [[round(x0, 4), round(y0, 4)], [round(x1, 4), round(y0, 4)], [round(x1, 4), round(y1, 4)], [round(x0, 4), round(y1, 4)]]


# --- runs -------------------------------------------------------------------------------------


async def _photos(session: AsyncSession, run: MockupRun) -> list[tuple[MockupPhoto, str]]:
    """The run's photos with their template's name; a photo deleted since, or no longer
    ready or confirmed, is left out."""
    found: list[tuple[MockupPhoto, str]] = []
    names: dict[uuid.UUID, str] = {}
    for raw in run.photo_ids:
        photo = await session.get(MockupPhoto, uuid.UUID(str(raw)))
        if photo is None or photo.tenant_id != run.tenant_id or photo.status != "ready" or photo.confirmed_at is None:
            continue
        if photo.template_id not in names:
            template = await session.get(MockupTemplate, photo.template_id)
            names[photo.template_id] = template.name if template is not None else photo.name
            _BACK_FALLBACK[photo.template_id] = template.back_without_design if template is not None else "front"
        found.append((photo, names[photo.template_id]))
    return found


#: Each template's "Back area without a back design", read with its photos for a run.
_BACK_FALLBACK: dict[uuid.UUID, str] = {}


def output_filename(design: MockupDesign, photo: MockupPhoto, template_name: str, prefixes: dict,
                    numbered: dict[tuple[uuid.UUID, uuid.UUID], int], taken: set[str]) -> str:
    """A mockup's file name: "<prefix>-<SKU>-<NN>.jpg" when its template has a prefix (NN: this
    design's mockups of that template, in photo order), else "<SKU>_<template>_<photo>.jpg"."""
    prefix = prefixes.get(photo.template_id)
    if prefix:
        key = (design.id, photo.template_id)
        numbered[key] = numbered.get(key, 0) + 1
        return mockups.prefixed_name(prefix, design_base(design), numbered[key], taken)
    return mockups.output_name(design_base(design), template_name, taken, photo.name)


def design_base(design: MockupDesign) -> str:
    """What a design's mockups are named after: its SKU, else its file name."""
    return design.parsed_sku or PurePosixPath(design.original_filename.replace("\\", "/")).stem or "design"


async def replace_earlier(session: AsyncSession, storage: Storage, batch_id: uuid.UUID) -> int:
    """Remove what an earlier run made for this batch: its outputs and their files,
    the listing photos made from them, and mockups made as photos before outputs
    existed. Only while nothing is written for the batch (the API checks)."""
    assets = list((await session.execute(
        select(Asset).where(Asset.batch_id == batch_id, Asset.mockup_design_id.is_not(None))
    )).scalars())
    outputs = list((await session.execute(select(MockupOutput).where(MockupOutput.batch_id == batch_id))).scalars())
    keys = {a.processed_key for a in assets if a.processed_key} | {o.key for o in outputs if o.key}
    for key in keys:
        await asyncio.to_thread(storage.delete_with_derivatives, key)
    if assets:
        await session.execute(delete(Asset).where(Asset.id.in_([a.id for a in assets])))
    if outputs:
        await session.execute(delete(MockupOutput).where(MockupOutput.id.in_([o.id for o in outputs])))
    return len(keys)


async def design_edits_for(session: AsyncSession, design_ids: list[uuid.UUID], photo_ids: list[uuid.UUID]) -> tuple[
        dict[tuple[uuid.UUID, uuid.UUID, int], dict], dict[tuple[uuid.UUID, str], list]]:
    """The seller's adjustments (per design, photo and area) and recolourings (per design and
    subgroup name, casefolded; "" = every subgroup) for these designs and photos."""
    adjust: dict[tuple[uuid.UUID, uuid.UUID, int], dict] = {}
    recolour: dict[tuple[uuid.UUID, str], list] = {}
    if design_ids and photo_ids:
        for row in (await session.execute(select(MockupAdjustment).where(
                MockupAdjustment.design_id.in_(design_ids), MockupAdjustment.photo_id.in_(photo_ids)))).scalars():
            adjust[(row.design_id, row.photo_id, row.area)] = row.data
    if design_ids:
        for row in (await session.execute(select(MockupRecolour).where(MockupRecolour.design_id.in_(design_ids)))).scalars():
            if row.regions:
                recolour[(row.design_id, (row.subgroup or "").casefold())] = row.regions
    return adjust, recolour


def regions_for(recolour: dict[tuple[uuid.UUID, str], list], design_id: uuid.UUID, subgroup: str | None) -> list | None:
    """A design's recoloured regions for one subgroup: its own, else those for every subgroup."""
    return recolour.get((design_id, (subgroup or "").casefold())) or recolour.get((design_id, "")) or None


async def run(session: AsyncSession, storage: Storage, run_id: uuid.UUID,
              refresh: list[tuple[uuid.UUID, uuid.UUID]] | None = None) -> MockupRun | None:
    """Make one run's mockups. Returns the run, or None when there is nothing to do.

    ``refresh``: (design, photo) pairs to make again after the seller adjusted or recoloured
    a design (api/mockup_edits.py): only those mockups are re-rendered, each keeping its
    record (a new file replaces the old one); nothing else of the run changes."""
    job = await session.get(MockupRun, run_id)
    if job is None or job.status not in ("queued", "running"):
        return job
    refreshing = refresh is not None
    wanted = {(uuid.UUID(str(d)), uuid.UUID(str(p))) for d, p in (refresh or [])}
    if refreshing:
        job.status, job.error = "running", None
    else:
        job.status, job.started_at, job.done, job.failed, job.error = "running", _now(), 0, 0, None
    all_photos = await _photos(session, job)
    positions = {p.id: i for i, (p, _) in enumerate(all_photos)}
    photos = [(p, n) for p, n in all_photos if not refreshing or any(p.id == pid for _, pid in wanted)]
    designs = list((await session.execute(
        select(MockupDesign)
        .where(MockupDesign.batch_id == job.batch_id, MockupDesign.design_key.is_not(None),
               MockupDesign.files_removed_at.is_(None))
        .order_by(MockupDesign.group_key, MockupDesign.original_filename)
    )).scalars())
    if job.design_ids is not None:  # the designs the seller ticked
        ticked = {str(x) for x in job.design_ids}
        designs = [d for d in designs if str(d.id) in ticked]
    existing: dict[tuple[uuid.UUID, uuid.UUID], MockupOutput] = {}
    if refreshing:
        for row in (await session.execute(select(MockupOutput).where(MockupOutput.run_id == job.id))).scalars():
            if row.photo_id is not None:
                existing[(row.design_id, row.photo_id)] = row
    else:
        await replace_earlier(session, storage, job.batch_id)
    await session.commit()

    tenant = await session.get(Tenant, job.tenant_id)
    cap = storage_cap.limit(tenant) if tenant is not None else None
    days = await upload_retention.policy(session)
    in_use = await asyncio.to_thread(storage_cap.used, storage, job.tenant_id)
    taken: set[str] = {o.filename.lower() for o in existing.values()}
    # A template's file-name prefix: "<prefix>-<SKU>-<NN>.jpg", numbered per design and template
    # in photo order (a re-render keeps each mockup's name and numbers new ones after them).
    prefixes = dict((await session.execute(select(MockupTemplate.id, MockupTemplate.file_prefix).where(
        MockupTemplate.id.in_({p.template_id for p, _ in all_photos} or {uuid.uuid4()})))).all())
    numbered: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
    if refreshing:
        for row in existing.values():
            photo_row = next((p for p, _ in all_photos if p.id == row.photo_id), None)
            if photo_row is not None:
                key_ = (row.design_id, photo_row.template_id)
                numbered[key_] = numbered.get(key_, 0) + 1

    try:
        areas = {p.id: photo_print_areas(p) for p, _ in photos}
        images: dict[uuid.UUID, Any] = {}
        usable: list[MockupDesign] = []
        # Each design's files for other subgroups (one design, one listing), loaded when used.
        versions: dict[uuid.UUID, list[MockupDesignVersion]] = {}
        if designs:
            for v in (await session.execute(
                select(MockupDesignVersion).where(MockupDesignVersion.design_id.in_([d.id for d in designs]),
                                                  MockupDesignVersion.design_key.is_not(None))
            )).scalars():
                versions.setdefault(v.design_id, []).append(v)
        adjustments, recolours = await design_edits_for(session, [d.id for d in designs], [p.id for p, _ in photos])
        loaded: dict[tuple[str, uuid.UUID, str], Any] = {}

        async def image_of(file: Any, back: bool, regions: list | None = None) -> Any:
            """A design file as printed: trimmed, recoloured (front files) as the seller set."""
            key = file.back_key if back else file.design_key
            if not key:
                return None
            regions = None if back else regions
            cache = ("back" if back else "front", file.id, json.dumps(regions, sort_keys=True) if regions else "")
            if cache not in loaded:
                loaded[cache] = await asyncio.to_thread(
                    mockups.load_design, storage.get(key), (file.trims or {}).get("back" if back else "front"), regions)
            return loaded[cache]

        template_groups = await template_subgroups(session, {p.template_id for p, _ in photos})
        for design in designs:
            try:
                images[design.id] = await image_of(design, False)
                if design.back_key:
                    await image_of(design, True)
                usable.append(design)
            except (mockups.MockupError, ImageProcessingError, FileNotFoundError) as exc:
                logger.info("mockup design %s could not be used: %s", design.id, exc)
                if not refreshing:
                    job.failed += len(photos)
                job.error = DESIGN_FAILED
                await session.commit()
        made = 0
        for photo, template_name in photos:
            position = positions[photo.id]
            # One photo's maps at a time (~60 MB each at 2000 px): a 20-photo run
            # holding them all peaked 0.6 GB higher.
            blank, mask = storage.get(photo.image_key or ""), storage.get(photo.mask_key or "")
            occluder = storage.get(photo.occluder_key) if photo.occluder_key and photo.occlusion else None
            garments = areas[photo.id]
            if not garments:
                continue
            roles = photo_area_roles(photo)
            behind = photo_area_occlusion(photo)
            fallback = _BACK_FALLBACK.get(photo.template_id, "front")
            # Each area's subgroup: the seller's link, the garment's tone (measured from the
            # photo for an area the analysis did not find), else the photo's.
            area_names = area_subgroup_names(photo, template_groups.get(photo.template_id, []),
                                             await asyncio.to_thread(measured_tones, blank, garments))
            if not any(prints_on(d, versions.get(d.id, []), area_names) for d in usable):
                if not refreshing:
                    job.total = max(0, job.total - len(usable))  # no design has a file for this photo's subgroup
                continue
            # Wherever an adjusted design reaches beyond its area, the maps cover it too.
            W, H = photo.width or 1, photo.height or 1
            reach = []
            for (did, pid, k), adj in adjustments.items():
                if pid == photo.id and k < len(garments) and did in images and images[did] is not None:
                    img = images[did]
                    r = mockups.design_reach([(x * W, y * H) for x, y in garments[k]], (img.width, img.height),
                                             photo.size, adj, W, H)
                    if r:
                        reach.append(r)
            maps = await asyncio.to_thread(mockups.build_maps, blank, mask, occluder, garments, reach)
            settings = mockups.Settings(garments[0], photo.size, photo.fold_strength, photo.occlusion)
            # Which designs go on one mockup: every design alone on all garments ("same"),
            # or one design per garment in order ("order").
            # A front & back photo always shows one design, front and back.
            # "Print on all garments" on this photo: the same design on every area, whatever the run says.
            if job.garment_mode == "order" and len(garments) > 1 and photo.layout != "front_back" and not photo.print_all:
                filled = order_groups(usable, len(garments))
                groups = [usable[i:i + len(garments)] for i in range(0, len(usable), len(garments))]
            else:
                groups = [[d] for d in usable]
                filled = [[d] * len(garments) for d in usable]
            for group, shown in zip(groups, filled):
                if refreshing and not any((d.id, photo.id) in wanted for d in group):
                    continue
                chosen = list(zip(shown, garments, roles, area_names, behind))
                # Each area's file: its subgroup's, else the design's own (every area renders).
                files = {d.id: files_for_areas(d, versions.get(d.id, []), area_names) for d in dict.fromkeys(shown)}
                placements = []
                for k, (d, a, r, n, o) in enumerate(chosen):
                    f = files[d.id][k]
                    if f is None:
                        continue
                    regions = regions_for(recolours, d.id, n)
                    front_img = await image_of(f, False, regions)
                    back_img = await image_of(f, True)
                    img = design_for(r, front_img, back_img, fallback)
                    if img is not None:
                        placements.append((img, a, o, adjustments.get((d.id, photo.id, k))))
                if not placements:
                    if not refreshing:
                        job.total = max(0, job.total - len(group))  # nothing of these designs belongs on this photo
                    continue
                try:
                    jpeg = await asyncio.to_thread(mockups.render_many, maps, placements, settings)
                except mockups.MockupError as exc:
                    if not refreshing:
                        job.failed += len(group)
                    job.error = f"{template_name} – {photo.name}: {exc}"
                    await session.commit()
                    continue
                if cap is not None and in_use + len(jpeg) > cap:
                    raise storage_cap.StorageFull(
                        storage_cap.message(in_use, cap, len(jpeg), days.drafted_days, days.unpublished_days)
                    )
                output_id = uuid.uuid4()
                key = f"{job.tenant_id}/{job.batch_id}/mockups/{output_id}.jpg"
                storage.put(key, jpeg, "image/jpeg")
                storage_cap.added(job.tenant_id, len(jpeg))
                in_use += len(jpeg)
                # One output per design shown (they share the file): each design's
                # listing and ZIP folder has the mockup it is printed on.
                replaced: set[str] = set()
                for k, design in enumerate(group):
                    row = existing.get((design.id, photo.id)) if refreshing else None
                    if row is not None:
                        if row.key and row.key != key:
                            replaced.add(row.key)
                        row.key, row.byte_size, row.width, row.height = key, len(jpeg), maps.width, maps.height
                        continue
                    session.add(MockupOutput(
                        id=output_id if k == 0 else uuid.uuid4(), tenant_id=job.tenant_id, run_id=job.id,
                        batch_id=job.batch_id, design_id=design.id, photo_id=photo.id,
                        template_name=template_name, photo_name=photo.name, position=position,
                        filename=output_filename(design, photo, template_name, prefixes, numbered, taken), key=key,
                        width=maps.width, height=maps.height, byte_size=len(jpeg),
                    ))
                    job.done += 1
                for old in replaced:  # the earlier file of a re-rendered mockup
                    await asyncio.to_thread(storage.delete_with_derivatives, old)
                made += 1
                await session.commit()
        job.status = "succeeded" if made or not job.total or refreshing else "failed"
    except storage_cap.StorageFull as exc:
        job.status, job.error = "failed", str(exc)
    except Exception:  # an unexpected fault: the run says so, the log has the rest
        logger.exception("mockup run %s failed", job.id)
        await session.rollback()
        job = await session.get(MockupRun, run_id)
        if job is None:
            return None
        job.status, job.error = "failed", "The mockups could not be made. Please try again."
    job.finished_at = _now()
    await session.commit()
    return job


async def generate_mockups(ctx: dict[str, Any], run_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await run(session, get_storage(), uuid.UUID(run_id))
        return done.status if done is not None else "missing"


async def rerender_mockups(ctx: dict[str, Any], run_id: str, pairs: list[list[str]]) -> str:
    """arq entrypoint: make chosen mockups of a run again (the seller's edits)."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await run(session, get_storage(), uuid.UUID(run_id), refresh=[(d, p) for d, p in pairs])
        return done.status if done is not None else "missing"


# --- use for listings -------------------------------------------------------------------------


async def outputs_by_design(session: AsyncSession, run_id: uuid.UUID) -> dict[uuid.UUID, list[MockupOutput]]:
    rows = (await session.execute(
        select(MockupOutput).where(MockupOutput.run_id == run_id).order_by(MockupOutput.position)
    )).scalars()
    found: dict[uuid.UUID, list[MockupOutput]] = {}
    for output in rows:
        found.setdefault(output.design_id, []).append(output)
    return found


async def make_listings(
    session: AsyncSession,
    job: MockupRun,
    design_ids: list[uuid.UUID] | None,
    covers: dict[uuid.UUID, uuid.UUID],
) -> tuple[int, int]:
    """The chosen designs (None: all) become listing groups: one per design, its
    mockups as its photos in the run's photo order, the chosen cover first. Using a
    design again replaces its group's photos (another cover, say). Returns
    (groups, photos). The caller commits."""
    by_design = await outputs_by_design(session, job.id)
    wanted = set(design_ids) if design_ids is not None else set(by_design)
    designs = {d.id: d for d in (await session.execute(
        select(MockupDesign).where(MockupDesign.id.in_(wanted), MockupDesign.batch_id == job.batch_id)
    )).scalars()}
    groups = photos = 0
    for design_id, outputs in by_design.items():
        design = designs.get(design_id)
        usable = [o for o in outputs if o.key and o.files_removed_at is None]
        if design is None or not usable:
            continue
        cover = covers.get(design_id)
        ordered = sorted(usable, key=lambda o: (0 if o.photo_id == cover else 1, o.position))
        await session.execute(delete(Asset).where(Asset.batch_id == job.batch_id, Asset.mockup_design_id == design_id))
        for rank, output in enumerate(ordered, start=1):
            session.add(Asset(
                batch_id=job.batch_id, tenant_id=job.tenant_id,
                original_filename=f"{design.group_key}/{output.filename}",
                parsed_sku=design.parsed_sku, group_key=design.group_key, upload_folder=design.group_key,
                processed_key=output.key, mime_type="image/jpeg", width=output.width, height=output.height,
                byte_size=output.byte_size, rank=rank, status=AssetStatus.processed,
                mockup_design_id=design.id, mockup_photo_id=output.photo_id,
            ))
            photos += 1
        groups += 1
    await session.flush()
    batch = await session.get(UploadBatch, job.batch_id)
    if batch is not None:
        assets = list((await session.execute(select(Asset).where(Asset.batch_id == batch.id))).scalars())
        _rerank(assets)
        batch.file_count = len(assets)
        batch.grouping_mode = batch.grouping_mode or "folder"
        if any(a.status is AssetStatus.processed for a in assets):
            batch.status = UploadBatchStatus.ready
    return groups, photos


# --- download ---------------------------------------------------------------------------------


async def build_zip(session: AsyncSession, storage: Storage, zip_id: uuid.UUID) -> MockupZip | None:
    """Write the ZIP (stored, not compressed: the JPEGs already are)."""
    archive = await session.get(MockupZip, zip_id)
    if archive is None or archive.status not in ("queued", "building"):
        return archive
    archive.status = "building"
    await session.commit()
    by_design = await outputs_by_design(session, archive.run_id)
    wanted = {uuid.UUID(str(i)) for i in archive.design_ids} if archive.design_ids is not None else set(by_design)
    designs = {d.id: d for d in (await session.execute(
        select(MockupDesign).where(MockupDesign.id.in_(wanted)))).scalars()}
    entries: list[tuple[str, str]] = []
    folders: set[str] = set()
    for design_id, outputs in by_design.items():
        design = designs.get(design_id)
        if design is None:
            continue
        folder = mockups.safe_name(design_base(design), 60)
        n, unique = 1, folder
        while unique.lower() in folders:
            n += 1
            unique = f"{folder} ({n})"
        folders.add(unique.lower())
        for output in outputs:
            if output.key and output.files_removed_at is None and storage.exists(output.key):
                entries.append((f"{unique}/{output.filename}" if archive.layout == "folders" else output.filename, output.key))
    tenant = await session.get(Tenant, archive.tenant_id)
    estimate = sum(len(storage.get(key)) for _, key in entries)  # a stored ZIP is its files plus headers
    if tenant is not None:
        cap = storage_cap.limit(tenant)
        in_use = await asyncio.to_thread(storage_cap.used, storage, archive.tenant_id)
        if in_use + estimate > cap:
            days = await upload_retention.policy(session)
            archive.status = "failed"
            archive.error = storage_cap.message(in_use, cap, estimate, days.drafted_days, days.unpublished_days)
            await session.commit()
            return archive
    if not entries:
        archive.status, archive.error = "failed", "There are no mockups to put in the ZIP."
        await session.commit()
        return archive

    def write() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            for name, key in entries:
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # the same bytes every time
                zf.writestr(info, storage.get(key))
        return buf.getvalue()

    data = await asyncio.to_thread(write)
    key = f"{archive.tenant_id}/zips/{archive.id}.zip"
    storage.put(key, data, "application/zip")
    storage_cap.added(archive.tenant_id, len(data))
    archive.key, archive.byte_size, archive.files = key, len(data), len(entries)
    archive.status, archive.ready_at = "ready", _now()
    archive.expires_at = archive.ready_at + timedelta(hours=MockupZip.RETENTION_HOURS)
    await session.commit()
    return archive


async def build_mockup_zip(ctx: dict[str, Any], zip_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await build_zip(session, get_storage(), uuid.UUID(zip_id))
        return done.status if done is not None else "missing"


async def expire_zips(session: AsyncSession, storage: Storage, now: datetime | None = None) -> int:
    """Delete every ZIP past its time (the file; the row says "expired")."""
    now = now or _now()
    rows = list((await session.execute(
        select(MockupZip).where(MockupZip.status == "ready", MockupZip.expires_at < now)
    )).scalars())
    for archive in rows:
        if archive.key:
            await asyncio.to_thread(storage.delete_with_derivatives, archive.key)
            storage_cap.forget(archive.tenant_id)
        archive.status, archive.key = "expired", None
    if rows:
        await session.commit()
    return len(rows)
