"""``python -m app.cli mockup-single-area [--apply] [--email <address>]``: one print area per
photo by default, for template photos prepared before it.

* A template where the seller confirmed a photo with several areas gets "This template has
  several garments in one photo" switched on: nothing of it changes.
* In every other template, a photo still waiting for the seller's check whose areas are all
  as suggested (``source`` "default") and that has more than one keeps only the main
  garment's area (the one marked main by the analysis, else the largest nearest the middle;
  ``mockups.chosen_rows``). All its suggestions are kept as candidates, so switching the
  template to several garments brings them back.
* Confirmed photos, and areas the seller moved, added or copied, are not touched.

No analysis runs (no model, no network). A dry run unless ``--apply``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, MockupTemplate, Tenant
from app.pipeline import mockups


def _as_suggested(photo: MockupPhoto) -> bool:
    return all((r.get("source") or "default") == "default" for r in (photo.garments or []))


async def run(sm: async_sessionmaker, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        query = select(MockupTemplate)
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupTemplate.tenant_id == tenant.id)
        templates = list((await session.execute(query.order_by(MockupTemplate.created_at))).scalars())
        switched = reduced = 0
        for template in templates:
            photos = list((await session.execute(
                select(MockupPhoto).where(MockupPhoto.template_id == template.id, MockupPhoto.status == "ready")
            )).scalars())
            if template.several_garments:
                continue
            if any(p.confirmed_at is not None and len(p.garments or []) > 1 for p in photos):
                switched += 1
                if apply:
                    template.several_garments = True
                continue
            for photo in photos:
                rows = photo.garments or []
                if photo.confirmed_at is not None or len(rows) <= 1 or not _as_suggested(photo):
                    continue
                reduced += 1
                if not apply:
                    continue
                analysis = dict(photo.analysis or {})
                candidates = analysis.get("candidates") or [dict(r) for r in rows]
                analysis["candidates"] = candidates
                chosen = mockups.chosen_rows(candidates, False)
                photo.analysis = analysis
                photo.garments = chosen
                photo.print_area, photo.print_area_source = chosen[0]["print_area"], "default"
                photo.version += 1
        if apply:
            await session.commit()
        verb = "" if apply else "would be "
        return (f"{reduced} waiting photo(s) {verb}reduced to their main garment's area; "
                f"{switched} template(s) with a confirmed several-area photo {verb}switched to several garments; "
                "confirmed photos untouched." + ("" if apply else " Run with --apply."))
