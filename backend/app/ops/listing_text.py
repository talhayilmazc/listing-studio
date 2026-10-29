"""``python -m app.cli listing-text <email> [--batch <id>] [--sku <sku>]``: where each listing's
text is stored and what each screen shows. READ ONLY: one read-only transaction, SELECTs only,
no Etsy call, nothing written.

Per listing: who wrote it; the listing's own title (first 40 characters), tag count and
description length; then for each shop: its own fields (if any), where the listing stands
there, and the text that shop gets (pipeline/shop_listing.py::text_for, the one reader the
workspace, the send summary, the draft and the go-live check use). Then what each screen
shows: the review card with no shop picked (the listing's text), the review card with each
shop picked (that shop's text only while the listing is on Etsy in another shop and not yet in
this one: lib/anotherShop.ts::editsShopText; otherwise the listing's text), the batch page's
group card (the listing's text) and Approve all (checks the listing's text, plus the shop in
view's own). An empty title or no tags is marked EMPTY.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Asset, EtsyConnection, GeneratedContent, Tenant, UploadBatch
from app.pipeline import shop_listing
from app.pipeline.batch_names import names_by_id

TITLE_CHARS = 40


def _text(title: str | None, tags: list[str] | None, description: str | None) -> str:
    title = title or ""
    shown = f"\"{title[:TITLE_CHARS]}\"{'…' if len(title) > TITLE_CHARS else ''}" if title else "EMPTY title"
    return f"{shown} · {len(tags or []) or 'EMPTY:'} {'tag' if len(tags or []) == 1 else 'tags'} · description {len(description or '')} chars"


def edits_shop_text(states: dict, shop_id: uuid.UUID) -> bool:
    """The review card edits this shop's own text (the TS rule, lib/anotherShop.ts::editsShopText)."""
    if states.get(shop_id, shop_listing.NOT_SENT) != shop_listing.NOT_SENT:
        return False
    return any(s != shop_listing.NOT_SENT for k, s in states.items() if k != shop_id)


async def _contents(session: AsyncSession, tenant: Tenant, batch: str | None, sku: str | None) -> list[GeneratedContent]:
    q = select(GeneratedContent).where(GeneratedContent.tenant_id == tenant.id)
    if batch:
        try:
            q = q.where(GeneratedContent.batch_id == uuid.UUID(batch.strip()))
        except ValueError:
            return []
    if sku:
        ids = select(Asset.id).where(Asset.tenant_id == tenant.id, func.lower(Asset.parsed_sku) == sku.strip().lower())
        q = q.where(GeneratedContent.asset_id.in_(ids))
    return list((await session.execute(q.order_by(GeneratedContent.batch_id, GeneratedContent.created_at))).scalars())


async def run(sm: async_sessionmaker, email: str, batch: str | None = None, sku: str | None = None) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            tenant = (await session.execute(select(Tenant).where(
                func.lower(Tenant.email) == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account {email!r}\n"
            contents = await _contents(session, tenant, batch, sku)
            shops = list((await session.execute(select(EtsyConnection).where(
                EtsyConnection.tenant_id == tenant.id).order_by(EtsyConnection.position))).scalars())
            label = {s.id: s.display_name or s.shop_name or str(s.id)[:8] for s in shops}
            head = (f"listing-text {tenant.email}" + (f" --batch {batch}" if batch else "") + (f" --sku {sku}" if sku else "")
                    + " (read only: no Etsy call, nothing written)")
            if not contents:
                return head + "\n\nno listings match\n"
            assets = {a.id: a for a in (await session.execute(select(Asset).where(
                Asset.id.in_([c.asset_id for c in contents])))).scalars()}
            names = await names_by_id(session, list({c.batch_id for c in contents}))
            states = await shop_listing.states(session, contents)
            lines = [head, ""]
            empty_anywhere = 0
            current_batch = None
            for c in contents:
                if c.batch_id != current_batch:
                    current_batch = c.batch_id
                    batch_row = await session.get(UploadBatch, c.batch_id)
                    deleted = " (deleted)" if batch_row is not None and getattr(batch_row, "deleted_at", None) else ""
                    lines.append(f"== batch {str(c.batch_id)[:8]} \"{names.get(c.batch_id) or '(no name)'}\"{deleted}")
                asset = assets.get(c.asset_id)
                lines.append(f"  listing {(asset.group_key if asset else '') or '?'} ({str(c.id)[:8]}) · SKU "
                             f"{(asset.parsed_sku if asset else None) or 'none'} · written "
                             f"{'by hand' if c.source == 'manual' else 'by the app'} · "
                             f"{'approved' if c.approved else 'not approved'}")
                lines.append(f"    stored, the listing's own: {_text(c.title, c.tags, c.description)}")
                mine = states.get(c.id, {})
                shown_empty = not c.title or not c.tags
                for shop in shops:
                    own = shop_listing.text_in(c, shop.id)
                    state = mine.get(shop.id, shop_listing.NOT_SENT)
                    title, tags, description = shop_listing.text_for(c, shop.id)
                    own_line = _text(own.get("title"), own.get("tags"), own.get("description")) if own else "none (follows the listing)"
                    lines.append(f"    {label[shop.id]} ({state.replace('_', ' ')}): its own fields: "
                                 f"{', '.join(sorted(own)) or 'none'}" + (f" = {own_line}" if own else ""))
                    lines.append(f"      goes to {label[shop.id]} as: {_text(title, tags, description)}"
                                 + (" · approve again for this shop" if shop_listing.edited_after_approval(c, shop.id) else ""))
                    shown_empty = shown_empty or not title or not tags
                lines.append("    screens:")
                lines.append(f"      review card, no shop picked: {_text(c.title, c.tags, c.description)}")
                for shop in shops:
                    if edits_shop_text(mine, shop.id):
                        t = shop_listing.text_for(c, shop.id)
                        lines.append(f"      review card, {label[shop.id]} picked: {_text(*t)} ({label[shop.id]}'s text)")
                    else:
                        lines.append(f"      review card, {label[shop.id]} picked: the listing's text")
                lines.append(f"      batch page group card: {_text(c.title, c.tags, c.description)}")
                for shop in shops:
                    if mine.get(shop.id, shop_listing.NOT_SENT) == shop_listing.NOT_SENT:
                        lines.append(f"      workspace / send summary / draft in {label[shop.id]}: "
                                     f"{_text(*shop_listing.text_for(c, shop.id))}")
                empty_anywhere += shown_empty
            lines += ["", f"{len(contents)} listing(s); {empty_anywhere} shown with an EMPTY title or no tags somewhere"]
            return "\n".join(lines) + "\n"
        finally:
            await session.rollback()
