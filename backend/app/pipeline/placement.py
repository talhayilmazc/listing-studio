"""A seller's own print placement, learnt from the photos they confirm (per account only).

When the seller presses "Looks right" (or "Confirm selected"), each garment of the photo
whose suggestion was found by the analysis becomes one ``mockup_placement`` row: the
garment's type and layout and where the confirmed area sits on it, relative to its chest
width (``print_rule.observe``; the geometry the suggestion was built from is kept with the
photo's garment, ``garments[i]["geometry"]``). The account's next photos of the same type
and layout are suggested from its own rows (``print_rule.personal_table``, blended with the
template standard by how many photos they come from). Not learnt from: an area the seller
replaced rather than adjusted (``print_rule.LEARN_MIN_IOU``), photos of more than
``print_rule.LEARN_MAX_GARMENTS`` garments, areas found from a tap or copied from another
photo without its own analysis. Rows are read only for their own account; "Reset" deletes
them. Only numbers are kept: no image, nothing from the photo itself.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MockupPhoto, MockupPlacement
from app.pipeline import print_rule

#: What each group is called on the Templates page.
LAYOUT_WORDS = {"worn": "worn", "folded": "folded", "flat": "laid flat", "hanger": "on a hanger"}
KIND_WORDS = {"t-shirt": "tees", "sweatshirt": "sweatshirts", "hoodie": "hoodies"}


def label(key: str) -> str:
    kind, _, layout = key.partition(" ")
    return f"{KIND_WORDS.get(kind, kind)} {LAYOUT_WORDS.get(layout, layout)}"


def geometry(found: Any, width: int, height: int) -> dict[str, Any]:
    """The landmarks a suggestion was built from (a ``garments.Found``), as shares of the
    analysed image, with the suggested corners: kept with the photo's garment so a later
    confirmation can be measured against them."""
    def share(p: tuple[float, float]) -> list[float]:
        return [round(p[0] / width, 5), round(p[1] / height, 5)]

    return {"neck": share(found.neck), "armpits": [share(p) for p in found.armpits],
            "angle": float(found.info.get("angle") or 0.0), "kind": found.kind, "layout": found.layout,
            "suggested": [share(p) for p in found.corners],
            "axis_estimate": found.info.get("axis_estimate")}


def observation(row: dict[str, Any], width: int, height: int) -> dict[str, Any] | None:
    """One garment row of a confirmed photo as an observation, or None when it cannot be
    learnt from."""
    geo = row.get("geometry")
    area = row.get("print_area")
    if not geo or not area or not width or not height:
        return None

    def px(p: list[float]) -> tuple[float, float]:
        return (p[0] * width, p[1] * height)

    try:
        return print_rule.observe(px(geo["neck"]), (px(geo["armpits"][0]), px(geo["armpits"][1])),
                                  [px(p) for p in area], kind=geo.get("kind"), layout=geo.get("layout"),
                                  angle=float(geo.get("angle") or 0.0),
                                  suggested=[px(p) for p in geo.get("suggested") or area])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def record(session: AsyncSession, photo: MockupPhoto) -> int:
    """The photo's rows, rewritten from its garments as they are now: one per learnable
    garment when the photo is confirmed, none otherwise. The caller commits."""
    await session.execute(delete(MockupPlacement).where(MockupPlacement.photo_id == photo.id))
    rows = photo.garments or []
    if photo.confirmed_at is None or not rows or len(rows) > print_rule.LEARN_MAX_GARMENTS:
        return 0
    written = 0
    for index, row in enumerate(rows):
        o = observation(row, photo.width or 0, photo.height or 0)
        if o is None:
            continue
        session.add(MockupPlacement(tenant_id=photo.tenant_id, photo_id=photo.id, garment=index, key=o["key"],
                                    width=o["width"], top=o["top"], aspect=o["aspect"]))
        written += 1
    return written


async def personal(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, dict[str, float]]:
    """The account's own ratios per garment type and layout (``print_rule.personal_table``)."""
    rows = (await session.execute(
        select(MockupPlacement.key, MockupPlacement.width, MockupPlacement.top, MockupPlacement.aspect)
        .where(MockupPlacement.tenant_id == tenant_id)
        .order_by(MockupPlacement.created_at, MockupPlacement.id)
    )).all()
    return print_rule.personal_table([{"key": k, "width": w, "top": t, "aspect": a} for k, w, t, a in rows])


async def summary(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """What the Templates page says: per group, from how many photos and whether it is used."""
    rows = (await session.execute(
        select(MockupPlacement.key, func.count()).where(MockupPlacement.tenant_id == tenant_id)
        .group_by(MockupPlacement.key)
    )).all()
    groups = [{"key": k, "label": label(k), "photos": int(n), "in_use": int(n) >= print_rule.PERSONAL_MIN}
              for k, n in sorted(rows)]
    photos = int((await session.execute(
        select(func.count(func.distinct(func.coalesce(MockupPlacement.photo_id, MockupPlacement.id))))
        .where(MockupPlacement.tenant_id == tenant_id)
    )).scalar_one())
    return {"photos": photos, "groups": groups}


async def reset(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Forget the account's placement: new photos are suggested from the standard. The
    caller commits."""
    result = await session.execute(delete(MockupPlacement).where(MockupPlacement.tenant_id == tenant_id))
    return int(result.rowcount or 0)


def built_from_own(photo: MockupPhoto) -> int | None:
    """From how many of the seller's photos this photo's suggestion was built (None: the standard)."""
    counts = [int(g.get("personal_n") or 0) for g in (photo.garments or [])
              if str(g.get("ratios") or "").startswith("yours")]
    return max(counts) if counts else None
