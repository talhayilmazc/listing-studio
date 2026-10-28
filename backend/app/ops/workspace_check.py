"""``python -m app.cli workspace-check <batch id | SKU> --shop <shop name>``: a batch's listings
as the shop workspace sees them for one shop. READ ONLY: one read-only transaction, SELECTs
only, no Etsy call, nothing written.

Per listing: its title there (first 40 characters), tag count and description length, and
where each comes from (the shop's own text or the listing's: pipeline/shop_listing.py::text_for,
the one rule every screen and the draft use); its SKU and profile there; approval for that shop
(approved; not approved; edited for that shop after approval); where it stands in that shop;
whether it can be sent and why not; and the production partner state.

A SKU names the batch that has a listing with it (each batch, if several do). The shop is
matched by its shop name or display name in that batch's account.
"""

from __future__ import annotations

import uuid
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.compliance.check import blocking_finding
from app.db.models import Asset, EtsyConnection, GeneratedContent, ListingProfile, Tenant, UploadBatch
from app.pipeline import shop_listing
from app.pipeline.batch_names import names_by_id

TITLE_CHARS = 40
SOURCE = {"shop": "the shop's own", "listing": "the listing's"}


async def _batches(session: AsyncSession, ref: str) -> list[UploadBatch]:
    try:
        batch = await session.get(UploadBatch, uuid.UUID(ref.strip()))
        return [batch] if batch is not None else []
    except ValueError:
        pass
    ids = list(dict.fromkeys((await session.execute(select(Asset.batch_id).where(
        func.lower(Asset.parsed_sku) == ref.strip().lower()))).scalars()))
    if not ids:
        return []
    return list((await session.execute(select(UploadBatch).where(UploadBatch.id.in_(ids))
                                       .order_by(UploadBatch.created_at.desc()))).scalars())


def _shop(shops: list[EtsyConnection], name: str) -> EtsyConnection | list[EtsyConnection] | None:
    wanted = name.strip().casefold()
    hits = [s for s in shops if wanted in {(s.shop_name or "").casefold(), (s.display_name or "").casefold()}]
    if not hits:
        hits = [s for s in shops if wanted in (s.shop_name or "").casefold() or wanted in (s.display_name or "").casefold()]
    return hits[0] if len(hits) == 1 else (hits or None)


async def _block(session: AsyncSession, batch: UploadBatch, shop_name: str) -> list[str]:
    from app.pipeline import profile_shops
    from app.pipeline.targets import check_text

    tenant = await session.get(Tenant, batch.tenant_id)
    shops = list((await session.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == batch.tenant_id)
                                        .order_by(EtsyConnection.position))).scalars())
    found = _shop(shops, shop_name)
    name = (await names_by_id(session, [batch.id])).get(batch.id) or "(no name)"
    head = f"== batch {str(batch.id)[:8]} \"{name}\" (account {tenant.email if tenant else '?'})"
    if found is None:
        return [head, f"  no shop called {shop_name!r} in this account: " + ", ".join(s.shop_name or "?" for s in shops)]
    if isinstance(found, list):
        return [head, f"  {shop_name!r} matches several shops: " + ", ".join(s.shop_name or "?" for s in found)]
    shop = found
    label = shop.display_name or shop.shop_name or "the shop"
    contents = list((await session.execute(select(GeneratedContent).where(
        GeneratedContent.batch_id == batch.id))).scalars())
    assets = {a.id: a for a in (await session.execute(select(Asset).where(
        Asset.id.in_([c.asset_id for c in contents] or [uuid.uuid4()])))).scalars()}
    contents.sort(key=lambda c: (assets[c.asset_id].group_key or "") if c.asset_id in assets else "")
    states = await shop_listing.states(session, contents)
    lines = [head, f"  shop {label} ({shop.status.value}) · {len(contents)} listing(s)"]
    counts: dict[str, int] = {}
    for content in contents:
        asset = assets.get(content.asset_id)
        title, tags, description = shop_listing.text_for(content, shop.id)
        source = shop_listing.text_source(content, shop.id)
        state = states[content.id].get(shop.id, shop_listing.NOT_SENT)
        profile_id = shop_listing.profile_in(content, shop.id) or content.listing_profile_id
        profile = await session.get(ListingProfile, profile_id) if profile_id else None
        link = await profile_shops.link_for(session, profile, shop.id) if profile is not None else None
        partner = shop_listing.partner_state(profile, link)
        sku = shop_listing.sku_in(content, asset.parsed_sku if asset else None, shop.id)
        if not content.approved:
            approval = "not approved"
        elif shop_listing.edited_after_approval(content, shop.id):
            approval = f"edited for {label} after approval: approve again"
        else:
            approval = "approved"
        check = await check_text(session, content, profile, title, tags, description)
        why: str | None = None
        if state != shop_listing.NOT_SENT:
            why = f"already in {label} ({state}): edited on Etsy"
        elif approval != "approved":
            why = approval
        elif asset is not None and asset.files_removed_at is not None:
            why = "its image files were deleted after the retention period"
        elif check.errors:
            why = "Etsy would refuse its text: " + "; ".join(check.errors)
        elif await blocking_finding(session, content):
            why = "a blocking compliance finding"
        elif partner == shop_listing.PARTNER_REQUIRED:
            why = f"Etsy requires a production partner (made by someone else): set one on profile {profile.name if profile else '?'}"
        elif profile is None:
            why = "no profile"
        verdict = "yes" if why is None else f"no: {why}"
        counts[verdict.split(":")[0]] = counts.get(verdict.split(":")[0], 0) + 1
        partner_line = {"ok": "ok", "note": f"note only (profile {profile.name if profile else '?'} names none; not needed "
                        "unless a partner makes it)", "required": "required, none set"}[partner]
        lines += [
            f"  listing {(asset.group_key if asset else '') or '?'} ({str(content.id)[:8]}) · written "
            f"{'by hand' if content.source == 'manual' else 'by the app'}",
            f"    title: \"{title[:TITLE_CHARS]}\"{'…' if len(title) > TITLE_CHARS else ''} ({len(title)} chars) from {SOURCE[source['title']]}",
            f"    tags: {len(tags)} from {SOURCE[source['tags']]} · description: {len(description)} chars from "
            f"{SOURCE[source['description']]}",
            f"    SKU in {label}: {sku or 'none'} ({'its own there' if shop_listing.sku_override(content, shop.id) else SOURCE['listing']})"
            f" · profile in {label}: {profile.name if profile else 'none'} "
            f"({'its own there' if shop_listing.profile_in(content, shop.id) else SOURCE['listing']})",
            f"    approval for {label}: {approval} · in {label}: {state.replace('_', ' ')}",
            f"    can be sent: {verdict}",
            f"    production partner: {partner_line}",
        ]
        if check.warnings:
            lines.append(f"    notes (never a stop): {len(check.warnings)}")
    lines.append("  summary: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return lines


async def run(sm: async_sessionmaker, ref: str, shop_name: str) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            batches = await _batches(session, ref)
            if not batches:
                return f"no batch with the id or a listing with the SKU {ref!r}"
            lines = [f"workspace-check {ref} --shop {shop_name} (read only: no Etsy call, nothing written)", ""]
            for batch in batches:
                lines += await _block(session, batch, shop_name)
                lines.append("")
            return "\n".join(lines).rstrip() + "\n"
        finally:
            await session.rollback()
