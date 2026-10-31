"""``python -m app.cli trademark-scan <email>``: the brands in one seller's listings, and what the
seller sees about each, with the trademark filter on and off. READ ONLY: one read-only transaction,
SELECTs only, no Etsy call, nothing written.

Every listing of that account (every batch, a deleted one marked as such, every shop): the listing's
own text and each shop's own text, read separately because each goes to Etsy separately. Only that
account's rows are read. Of the seller's text: the first 40 characters of a title and the way a brand
was written, nothing more.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.compliance.trademarks import (
    Blocklist,
    all_marks,
    brand_warnings,
    filter_on,
    list_date,
    spot_in_listing,
    trademark_summary,
)
from app.db.models import EtsyConnection, GeneratedContent, Tenant, UploadBatch
from app.pipeline import shop_listing
from app.pipeline.batch_names import names_for


def _where(title: str, tags: list[str], description: str) -> list[str]:
    out = []
    for place, spotted in spot_in_listing(title, tags, description).items():
        out += [f"{s.term} in the {'tags' if place == 'tags' else place}" + (f' (written "{s.written}")' if s.written != s.term else "")
                for s in spotted]
    return list(dict.fromkeys(out))


def _sees(title: str, tags: list[str], description: str, blocklist: Blocklist) -> str:
    """What the card, the workspace row and the send summary say with this filter."""
    stopped = trademark_summary(title, tags, description, blocklist)
    warned = brand_warnings(title, tags, description, blocklist)
    parts = []
    if stopped:
        parts.append(f'blocked: "{stopped}"')
    if warned:
        brands = ", ".join(w.split(" is a trademark", 1)[0] for w in warned)
        parts.append(f"warned, can send: {brands}")
    return "; ".join(parts) or "nothing"


def _texts(content: GeneratedContent, shops: dict[str, str]) -> list[tuple[str, str, list[str], str]]:
    """The listing's text, then each shop's own text where it has one."""
    out = [("the listing's own text", content.title or "", list(content.tags or []), content.description or "")]
    for shop_id in (content.shop_overrides or {}):
        if shop_listing.text_in(content, shop_id):
            title, tags, description = shop_listing.text_for(content, shop_id)
            out.append((f"its text for {shops.get(str(shop_id), 'a shop no longer connected')}", title, list(tags), description))
    return out


async def scan(session: AsyncSession, email: str) -> str:
    tenant = (await session.execute(select(Tenant).where(func.lower(Tenant.email) == email.strip().lower()))).scalar_one_or_none()
    if tenant is None:
        return f"no account with the email {email!r}"
    shops = {str(c.id): c.display_name or c.shop_name or "a shop" for c in (await session.execute(
        select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id))).scalars()}
    batches = {b.id: b for b in (await session.execute(select(UploadBatch).where(UploadBatch.tenant_id == tenant.id))).scalars()}
    names = await names_for(session, list(batches.values()))
    contents = list((await session.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == tenant.id)
                                           .order_by(GeneratedContent.batch_id, GeneratedContent.created_at))).scalars())
    full, none = all_marks(), Blocklist()
    lines = [f"trademark-scan {email} (read only: no Etsy call, nothing written)",
             f"The list: {len(full.terms)} names, reviewed {list_date() or 'on no recorded day'}.",
             f"This account's filter is {'ON' if filter_on(tenant) else 'OFF'}.",
             f"Listings: {len(contents)}.", ""]
    found: Counter[str] = Counter()
    listed = 0
    for content in contents:
        block: list[str] = []
        for label, title, tags, description in _texts(content, shops):
            where = _where(title, tags, description)
            if not where:
                continue
            for term in {s.term for spotted in spot_in_listing(title, tags, description).values() for s in spotted}:
                found[term] += 1
            block.append(f"  {label}: \"{title[:40]}\"")
            block.extend(f"    {w}" for w in where)
            block.append(f"    filter on: {_sees(title, tags, description, full)}")
            block.append(f"    filter off: {_sees(title, tags, description, none)}")
        if block:
            listed += 1
            batch = batches.get(content.batch_id)
            gone = " (deleted batch)" if batch is not None and batch.deleted_at is not None else ""
            lines.append(f"listing {str(content.id)[:8]} in \"{names.get(content.batch_id, 'a batch')}\"{gone}")
            lines.extend(block)
    if listed:
        lines.insert(4, f"With a listed name: {listed}. Most often: " + ", ".join(f"{t} ({n})" for t, n in found.most_common(10)) + ".")
    else:
        lines.insert(4, "With a listed name: none.")
    return "\n".join(lines).rstrip()


async def run(sm: async_sessionmaker, email: str) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            return await scan(session, email)
        finally:
            await session.rollback()

