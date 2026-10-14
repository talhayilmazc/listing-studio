"""Occasion, Holiday and Section on the review card (pipeline/item_options.py).

Everything is per account: another account's listing, batch or shop is 404.
Occasion / Holiday values come only from Etsy's list for the listing's category
(the profile's payload, within its 24-hour refresh). A shop's sections are read
through the queue (``sync_shop_sections``) and kept 24 hours; a section is created
only when the seller confirms (``create_shop_section``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shops import shop_label
from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    Tenant,
    UploadBatch,
)
from app.etsy.shops import active_shops, owned_shop
from app.pipeline import item_options as opts
from app.pipeline.group_state import group_states

router = APIRouter(prefix="/api", tags=["item-options"])

#: Other Etsy content (a shop's sections) may be kept 24 hours.
SECTIONS_MAX_AGE = timedelta(hours=24)
#: Etsy limits a section title to 24 characters.
SECTION_TITLE_MAX = 24


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fresh_sections(connection: EtsyConnection) -> list[dict[str, Any]] | None:
    """The shop's sections while within 24 hours; None when they must be read again."""
    at = _utc(connection.sections_at)
    if connection.sections is None or at is None or datetime.now(timezone.utc) - at > SECTIONS_MAX_AGE:
        return None
    return list(connection.sections)


async def ask_for_sections(enqueuer: Enqueuer, connection: EtsyConnection) -> None:
    # One read per shop at a time, however many cards ask (arq keeps one job per id).
    await enqueuer.enqueue("sync_shop_sections", str(connection.id), _job_id=f"sections:{connection.id}")


async def _own_content(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="listing not found")
    return content


async def _profile(session: AsyncSession, tenant: Tenant, content: GeneratedContent) -> ListingProfile | None:
    if content.listing_profile_id is None:
        return None
    profile = await session.get(ListingProfile, content.listing_profile_id)
    return profile if profile is not None and profile.tenant_id == tenant.id else None


async def target_shops(session: AsyncSession, tenant: Tenant, content: GeneratedContent) -> list[EtsyConnection]:
    """The shops the listing goes to: the one it was written for, those it is
    planned or distributed to, and those it has a draft in (pipeline/group_state.py)."""
    asset = await session.get(Asset, content.asset_id)
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    targets: set[uuid.UUID] = set()
    if asset is not None:
        state = (await group_states(session, batch_ids={content.batch_id}, with_removed=True)).get(
            (content.batch_id, asset.group_key or "")
        )
        if state is not None:
            targets = set(state.targets)
    if not targets:
        batch = await session.get(UploadBatch, content.batch_id)
        if batch is not None and batch.connection_id is not None:
            targets.add(batch.connection_id)
    return [shops[c] for c in shops if c in targets]


def _property(payload: dict[str, Any], content: GeneratedContent, name: str, default: str | None) -> schemas.PropertyOptionsOut | None:
    options = opts.options_for(payload, name)
    if options is None:
        return None
    selected, source = opts.chosen(content.attributes, content.item_options, options.name)
    if source != "seller":
        # The writer's choice under the category's own spelling of the property.
        selected, source = opts.chosen(content.attributes, content.item_options, name)
    return schemas.PropertyOptionsOut(
        name=options.name, values=options.values, max_values=options.max_values,
        selected=[v for v in selected if v.lower() in {x.lower() for x in options.values}],
        source=source, profile_default=default,
    )


def _shop_out(content: GeneratedContent, shop: EtsyConnection, profile: ListingProfile | None, names: dict[str, str]) -> schemas.ShopSectionOut:
    sections = fresh_sections(shop)
    out = schemas.ShopSectionOut(
        connection_id=shop.id, shop_name=shop_label(shop), can_create="shops_w" in (shop.scopes or []),
        sections=[schemas.SectionOptionOut(id=s["id"], title=s["title"]) for s in sections] if sections is not None else None,
    )
    if sections is None:
        out.reason = "reading this shop's sections"
        return out
    by_id = {s["id"]: s["title"] for s in sections}
    by_title = {s["title"].lower(): s for s in sections}
    own = (content.item_options or {}).get(opts.SECTIONS) or {}
    mine = own.get(str(shop.id))
    if mine is not None:
        out.source = "seller"
        if mine.get("id") is None:
            out.reason = "no section, as you chose"
            return out
        if int(mine["id"]) in by_id:
            out.selected_id, out.selected_title = int(mine["id"]), by_id[int(mine["id"])]
            return out
        out.missing_title, out.reason = mine.get("title"), "your choice is no longer in this shop"
    else:
        carried = next(((cid, s) for cid, s in own.items() if s and s.get("title")), None)
        if carried is not None:
            title = str(carried[1]["title"])
            if title.lower() in by_title:
                hit = by_title[title.lower()]
                out.selected_id, out.selected_title, out.source = hit["id"], hit["title"], "carried"
                out.reason = f"same as in {names.get(carried[0], 'your other shop')}"
                return out
            out.missing_title = title
    picked = opts.suggest_section(
        [s["title"] for s in sections], theme_words=opts.theme_words(content.attributes),
        occasion=str(((content.attributes or {}).get("vision") or {}).get("occasion") or ""),
        profile_name=profile.name if profile is not None else "",
    )
    if picked.title is not None:
        hit = by_title[picked.title.lower()]
        out.selected_id, out.selected_title, out.source = hit["id"], hit["title"], "suggested"
    elif out.source == "seller":
        out.source = "none"
    out.reason = (f"{out.reason}; " if out.reason else "") + picked.reason
    return out


async def options_out(session: AsyncSession, tenant: Tenant, content: GeneratedContent, enqueuer: Enqueuer) -> schemas.ItemOptionsOut:
    profile = await _profile(session, tenant, content)
    payload = (profile.cached_payload or {}) if profile is not None else {}
    shops = await target_shops(session, tenant, content)
    for shop in shops:
        if fresh_sections(shop) is None:
            await ask_for_sections(enqueuer, shop)
    names = {str(s.id): shop_label(s) for s in shops}
    out = schemas.ItemOptionsOut(
        content_id=content.id,
        occasion=_property(payload, content, opts.OCCASION, profile.default_occasion if profile else None),
        holiday=_property(payload, content, opts.HOLIDAY, profile.default_holiday if profile else None),
        shops=[_shop_out(content, s, profile, names) for s in shops],
    )
    if out.occasion is None and out.holiday is None:
        out.note = (
            "Occasion and Holiday appear once the listing's profile has read its category's lists from Etsy"
            if profile is not None and not payload.get("category_attributes")
            else "This listing's category has no Occasion or Holiday on Etsy"
        )
    return out


async def _apply(session: AsyncSession, tenant: Tenant, content: GeneratedContent, fields: set[str],
                 occasion: list[str] | None, holiday: list[str] | None,
                 sections: dict[uuid.UUID, int | str | None] | None) -> None:
    profile = await _profile(session, tenant, content)
    payload = (profile.cached_payload or {}) if profile is not None else {}
    own = dict(content.item_options or {})
    for field, name, values in (("occasion", opts.OCCASION, occasion), ("holiday", opts.HOLIDAY, holiday)):
        if field not in fields:
            continue
        if values is None:
            own.pop(name, None)  # back to the writer's choice
            continue
        try:
            own[name] = opts.validate(values, opts.options_for(payload, name), name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if sections:
        chosen = dict(own.get(opts.SECTIONS) or {})
        targets = {s.id: s for s in await target_shops(session, tenant, content)}
        for connection_id, pick in sections.items():
            shop = await owned_shop(session, tenant.id, connection_id)
            if shop is None:
                raise HTTPException(status_code=404, detail="shop not found")
            if shop.id not in targets:
                raise HTTPException(status_code=422, detail=f"This listing does not go to {shop_label(shop)}.")
            if pick == "default":
                chosen.pop(str(shop.id), None)
                continue
            if pick is None:
                chosen[str(shop.id)] = {"id": None, "title": None}
                continue
            listed = fresh_sections(shop)
            if listed is None:
                raise HTTPException(status_code=409, detail=f"{shop_label(shop)}'s sections are being read; try again in a moment.")
            hit = next((s for s in listed if s["id"] == int(pick)), None)
            if hit is None:
                raise HTTPException(status_code=422, detail=f"That section is not in {shop_label(shop)}.")
            chosen[str(shop.id)] = {"id": hit["id"], "title": hit["title"]}
        own[opts.SECTIONS] = chosen
    content.item_options = own or None


@router.get("/content/{content_id}/options", response_model=schemas.ItemOptionsOut)
async def get_options(
    content_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ItemOptionsOut:
    return await options_out(session, tenant, await _own_content(session, tenant, content_id), enqueuer)


@router.put("/content/{content_id}/options", response_model=schemas.ItemOptionsOut)
async def set_options(
    content_id: uuid.UUID,
    body: schemas.ItemOptionsUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ItemOptionsOut:
    """Set the listing's Occasion, Holiday and Section per shop; they go with it to
    every shop and are written on its drafts."""
    content = await _own_content(session, tenant, content_id)
    await _apply(session, tenant, content, body.model_fields_set, body.occasion, body.holiday, body.sections)
    await session.commit()
    return await options_out(session, tenant, content, enqueuer)


@router.post("/batches/{batch_id}/options", response_model=schemas.ItemOptionsBulkOut)
async def set_options_for_selected(
    batch_id: uuid.UUID,
    body: schemas.ItemOptionsBulk,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ItemOptionsBulkOut:
    """"Set for selected": the same Occasion / Holiday / Section for each listing.
    A listing whose category does not offer a value, or that does not go to the
    shop, is left as it is and said why."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    fields = body.model_fields_set & {"occasion", "holiday"}
    sections = None
    if "section_id" in body.model_fields_set:
        if body.section_connection_id is None:
            raise HTTPException(status_code=422, detail="Choose the shop the section is in.")
        if await owned_shop(session, tenant.id, body.section_connection_id) is None:
            raise HTTPException(status_code=404, detail="shop not found")
        sections = {body.section_connection_id: body.section_id}
    updated, skipped = 0, []
    for content_id in dict.fromkeys(body.content_ids):
        content = await session.get(GeneratedContent, content_id)
        if content is None or content.tenant_id != tenant.id or content.batch_id != batch_id:
            skipped.append(schemas.BulkSkipped(content_id=content_id, reason="not a listing of this batch"))
            continue
        try:
            await _apply(session, tenant, content, fields, body.occasion, body.holiday, sections)
        except HTTPException as exc:
            skipped.append(schemas.BulkSkipped(content_id=content_id, reason=str(exc.detail)))
            continue
        updated += 1
    await session.commit()
    return schemas.ItemOptionsBulkOut(updated=updated, skipped=skipped)


@router.post("/shops/{connection_id}/sections", response_model=schemas.SectionCreateOut)
async def create_section(
    connection_id: uuid.UUID,
    body: schemas.SectionCreate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.SectionCreateOut:
    """"Create section '<name>' in <shop>": nothing is created until ``confirm``.
    The listings named get it as their section in that shop once it exists."""
    shop = await owned_shop(session, tenant.id, connection_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    title = " ".join(body.title.split())
    if not title or len(title) > SECTION_TITLE_MAX:
        raise HTTPException(status_code=422, detail=f"A section title is 1 to {SECTION_TITLE_MAX} characters on Etsy.")
    for cid in body.content_ids:
        await _own_content(session, tenant, cid)
    name = shop_label(shop)
    existing = next((s for s in fresh_sections(shop) or [] if s["title"].lower() == title.lower()), None)
    if existing is not None:
        # It is there already: chosen, nothing created.
        for cid in body.content_ids:
            content = await _own_content(session, tenant, cid)
            await _apply(session, tenant, content, set(), None, None, {shop.id: existing["id"]})
        await session.commit()
        return schemas.SectionCreateOut(queued=False, title=existing["title"], shop_name=name, requests=0,
                                        message=f'"{existing["title"]}" is already a section in {name}; chosen.')
    if "shops_w" not in (shop.scopes or []):
        raise HTTPException(
            status_code=409,
            detail=f"{name} has not given this app permission to create sections. Reconnect it, or create the section in Shop Manager.",
        )
    if not body.confirm:
        return schemas.SectionCreateOut(
            queued=False, title=title, shop_name=name,
            message=f'Create the section "{title}" in {name}? It takes about 3 Etsy requests from your daily limit.',
        )
    from app.workers.sections import create_spec

    spec = create_spec(shop.id, title, body.content_ids)
    await enqueuer.enqueue("create_shop_section", spec, _job_id=f"create-section:{shop.id}:{title.lower()}")
    return schemas.SectionCreateOut(queued=True, title=title, shop_name=name,
                                    message=f'Creating "{title}" in {name}; it is chosen for these listings once it exists.')
