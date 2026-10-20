"""``python -m app.cli mockup-colours [--apply] [--email <address>]``: the colour group (light or
dark) of template photos prepared before design variants existed (migration 0081).

Each ready photo with no colour group yet is measured from its garment's lightness under its
first print area (pipeline/variants.py; no model, no network); a photo close to the line is
marked for the seller to choose. A colour the seller set is never touched. A photo used for
mockups before this runs is measured then, so this only saves the seller seeing "Light or
dark?" on photos the app can read. A dry run unless ``--apply``.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MockupPhoto, Tenant
from app.pipeline.storage import Storage
from app.workers import mockups as work


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None) -> str:
    async with sm() as session:
        query = select(MockupPhoto).where(MockupPhoto.status == "ready", MockupPhoto.colour.is_(None),
                                          MockupPhoto.colour_source.is_(None))
        if email:
            tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account with the address {email}"
            query = query.where(MockupPhoto.tenant_id == tenant.id)
        photos = list((await session.execute(query.order_by(MockupPhoto.created_at))).scalars())
        if not apply:
            return f"{len(photos)} photo(s) without a colour group; run with --apply to measure them"
        await work.ensure_colours(session, storage, photos)
        found = Counter(f"{p.colour or 'unread'}{' (uncertain)' if p.colour_source == 'uncertain' else ''}" for p in photos)
        await session.commit()
        parts = ", ".join(f"{n} {k}" for k, n in sorted(found.items())) or "none"
        return f"measured {len(photos)} photo(s): {parts}"
