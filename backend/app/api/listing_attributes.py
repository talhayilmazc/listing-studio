"""A listing's attributes: every one Etsy offers for its category, set by the seller (pipeline/listing_attributes.py).

``GET  /api/content/{id}/attributes[?shop=]``  the category's attributes, each with Etsy's values (or units), the value
                                              the listing has (in that shop when given) and whose it is.
``PUT  /api/content/{id}/attributes``          set values, on the listing or (``connection_id``) for one shop only;
                                              a value of None goes back (to the suggestion, or to the listing's own);
                                              ``keep``: the shop follows the listing again ("Keep as it is").
``POST /api/batches/{id}/attributes/bulk``     "Set <attribute> to <value>" for selected listings (for one shop with
                                              ``connection_id``); ``apply`` false is the preview, which says which
                                              listings change and which are skipped and why.

Only Etsy's values are accepted. Drafts send them; a listing already on Etsy in a shop keeps its attributes there (this
is for new drafts only, and the answer says so). Nothing here calls Etsy. The caller's own listings and shops only (404).
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.api.shops import shop_label
from app.core import audit
from app.db.models import EtsyConnection, GeneratedContent, ListingProfile, Tenant, UploadBatch
from app.etsy.shops import active_shops, owned_shop
from app.pipeline import listing_attributes as LA
from app.pipeline import shop_listing

router = APIRouter(prefix="/api", tags=["listing-attributes"])

LIVE_NOTE = "Already on Etsy in {shops}: these values go to new drafts only. The listing on Etsy keeps its attributes."
SOURCE_WORDS = {LA.SHOP: "set for this shop", LA.SELLER: "set by you", LA.WRITER: "suggested", LA.PROFILE: "from your profile",
                LA.NONE: "not set"}


class PropertyOut(BaseModel):
    name: str
    kind: str
    max_values: int
    required: bool
    #: Etsy's values (choice), each one's unit where it has one; Etsy's units (number, or choices by unit).
    values: list[str] = Field(default_factory=list)
    value_units: dict[str, str] = Field(default_factory=dict)
    units: list[str] = Field(default_factory=list)
    current: list[str] = Field(default_factory=list)
    unit: str | None = None
    #: seller / shop / writer / profile / none
    source: str
    source_words: str
    #: The writer's suggestion, shown beside what the seller set.
    suggested: list[str] = Field(default_factory=list)


class DroppedOut(BaseModel):
    name: str
    values: list[str]


class AttributesOut(BaseModel):
    content_id: uuid.UUID
    shop_id: uuid.UUID | None = None
    shop_name: str | None = None
    properties: list[PropertyOut]
    #: The seller's values this category does not offer: kept, never sent.
    dropped: list[DroppedOut] = Field(default_factory=list)
    note: str | None = None
    #: The profile has not read its category's attributes yet.
    waiting: bool = False


class ValueIn(BaseModel):
    values: list[str] = Field(default_factory=list, max_length=100)
    unit: str | None = None


class AttributesIn(BaseModel):
    #: One shop only; omitted: the listing itself (every shop that follows it).
    connection_id: uuid.UUID | None = None
    #: Attribute name -> its value ([] = not set); None = back to the suggestion (listing) or the listing's own (shop).
    values: dict[str, ValueIn | None] = Field(default_factory=dict, max_length=100)
    keep: bool = False


class BulkIn(BaseModel):
    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    connection_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    values: list[str] = Field(default_factory=list, max_length=100)
    unit: str | None = None
    apply: bool = False


class BulkRow(BaseModel):
    content_id: uuid.UUID
    label: str
    before: list[str]
    after: list[str]
    changed: bool
    reason: str | None = None
    note: str | None = None


class BulkOut(BaseModel):
    rows: list[BulkRow]
    changed: int
    skipped: int
    applied: bool
    sentence: str


async def _content(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    batch = await session.get(UploadBatch, content.batch_id) if content is not None else None
    if content is None or content.tenant_id != tenant.id or (batch is not None and batch.deleted_at is not None):
        raise HTTPException(status_code=404, detail="listing not found")
    return content


async def _shop(session: AsyncSession, tenant: Tenant, shop_id: uuid.UUID | None) -> EtsyConnection | None:
    if shop_id is None:
        return None
    shop = await owned_shop(session, tenant.id, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    return shop


async def _profile(session: AsyncSession, tenant: Tenant, content: GeneratedContent, shop: EtsyConnection | None) -> ListingProfile | None:
    """The profile the listing goes with (in that shop: its own there, else its own): its category is the list."""
    profile_id = (shop_listing.profile_in(content, shop.id) if shop is not None else None) or content.listing_profile_id
    profile = await session.get(ListingProfile, profile_id) if profile_id else None
    return profile if profile is not None and profile.tenant_id == tenant.id else None


async def _live_in(session: AsyncSession, tenant: Tenant, content: GeneratedContent, shop: EtsyConnection | None) -> list[str]:
    states = (await shop_listing.states(session, [content]))[content.id]
    shops = {s.id: s for s in await active_shops(session, tenant.id)}
    there = [shops[s] for s in shops if shop_listing.sent(states, s)]
    return [shop_label(s) for s in there if shop is None or s.id == shop.id]


def _out_property(content: GeneratedContent, prop: LA.Prop, shop: EtsyConnection | None, reference: list[dict] | None) -> PropertyOut:
    now = LA.current(content, prop, shop.id if shop else None, reference)
    hit, raw = LA._by_name(((content.attributes or {}).get("listing") or {}), prop.name)
    return PropertyOut(
        name=prop.name, kind=prop.kind, max_values=prop.max_values, required=prop.required, values=prop.values,
        value_units=prop.value_scale, units=prop.scales, current=now.values, unit=now.scale, source=now.source,
        source_words=SOURCE_WORDS[now.source], suggested=LA.parse(raw)[0] if hit else [],
    )


async def attributes_out(session: AsyncSession, tenant: Tenant, content: GeneratedContent, shop: EtsyConnection | None) -> AttributesOut:
    profile = await _profile(session, tenant, content, shop)
    payload = (profile.cached_payload or {}) if profile is not None else {}
    props = LA.properties_of(payload)
    reference = payload.get("attributes") if isinstance(payload.get("attributes"), list) else None
    live = await _live_in(session, tenant, content, shop)
    return AttributesOut(
        content_id=content.id, shop_id=shop.id if shop else None, shop_name=shop_label(shop) if shop else None,
        properties=[_out_property(content, p, shop, reference) for p in props],
        dropped=[DroppedOut(name=n, values=v) for n, v in LA.dropped(content, props, shop.id if shop else None)],
        note=LIVE_NOTE.format(shops=", ".join(live)) if live else None,
        waiting=profile is not None and not props,
    )


@router.get("/content/{content_id}/attributes", response_model=AttributesOut)
async def get_attributes(content_id: uuid.UUID, shop: uuid.UUID | None = None,
                         session: AsyncSession = Depends(get_session, scope="function"),
                         tenant: Tenant = Depends(active_tenant)) -> AttributesOut:
    content = await _content(session, tenant, content_id)
    return await attributes_out(session, tenant, content, await _shop(session, tenant, shop))


def _set(content: GeneratedContent, shop: EtsyConnection | None, props: list[LA.Prop], name: str, value: ValueIn | None) -> None:
    """One attribute's value, checked against Etsy's list for this category (ValueError: why not)."""
    prop = LA.find(props, name)
    if value is None:
        stored = None
        name = prop.name if prop is not None else name
    else:
        values, unit = LA.check(prop, value.values, value.unit, name)
        stored = LA.stored(values, unit)
        name = prop.name if prop is not None else name
    if shop is None:
        sections = (content.item_options or {}).get(LA.opts.SECTIONS)
        mine = LA.set_value(LA.own(content), name, stored)
        if sections is not None:
            mine[LA.opts.SECTIONS] = sections
        content.item_options = mine or None
    else:
        shop_listing.set_override(content, shop.id, attributes=LA.set_value(LA.shop_own(content, shop.id), name, stored) or None)


@router.put("/content/{content_id}/attributes", response_model=AttributesOut)
async def set_attributes(content_id: uuid.UUID, body: AttributesIn,
                         session: AsyncSession = Depends(get_session, scope="function"),
                         tenant: Tenant = Depends(active_tenant)) -> AttributesOut:
    content = await _content(session, tenant, content_id)
    shop = await _shop(session, tenant, body.connection_id)
    if body.keep:
        if shop is None:
            raise HTTPException(status_code=422, detail="Keep as it is is for one shop's values.")
        shop_listing.set_override(content, shop.id, attributes=None)
    else:
        profile = await _profile(session, tenant, content, shop)
        props = LA.properties_of((profile.cached_payload or {}) if profile is not None else {})
        for name, value in body.values.items():
            try:
                _set(content, shop, props, name, value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.record(session, "listing.attributes_changed", actor=tenant, target_tenant_id=tenant.id, content_id=str(content.id),
                 connection_id=str(shop.id) if shop else None, names=sorted(body.values), keep=body.keep)
    await session.commit()
    return await attributes_out(session, tenant, content, shop)


@router.post("/batches/{batch_id}/attributes/bulk", response_model=BulkOut)
async def bulk_attributes(batch_id: uuid.UUID, body: BulkIn,
                          session: AsyncSession = Depends(get_session, scope="function"),
                          tenant: Tenant = Depends(active_tenant)) -> BulkOut:
    """"Set <attribute> to <value>" for the selected listings. A listing whose category has no such attribute, or
    does not offer that value, is skipped and said why; nothing is saved unless ``apply``."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id or batch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="batch not found")
    shop = await _shop(session, tenant, body.connection_id)
    rows: list[BulkRow] = []
    for content_id in dict.fromkeys(body.content_ids):
        content = await session.get(GeneratedContent, content_id)
        if content is None or content.tenant_id != tenant.id or content.batch_id != batch.id:
            raise HTTPException(status_code=404, detail="listing not found")
        profile = await _profile(session, tenant, content, shop)
        payload = (profile.cached_payload or {}) if profile is not None else {}
        props = LA.properties_of(payload)
        prop = LA.find(props, body.name)
        label = (content.title or "")[:40] or "Untitled"
        live = await _live_in(session, tenant, content, shop)
        note = LIVE_NOTE.format(shops=", ".join(live)) if live else None
        if prop is None:
            rows.append(BulkRow(content_id=content.id, label=label, before=[], after=[], changed=False, note=note,
                                reason=f"its category has no {body.name}" if props else "its profile has not read its category's attributes yet"))
            continue
        reference = payload.get("attributes") if isinstance(payload.get("attributes"), list) else None
        before = LA.current(content, prop, shop.id if shop else None, reference)
        try:
            after, unit = LA.check(prop, body.values, body.unit, prop.name)
        except ValueError as exc:
            rows.append(BulkRow(content_id=content.id, label=label, before=before.values, after=before.values, changed=False,
                                reason=str(exc), note=note))
            continue
        changed = (after, unit) != (before.values, before.scale) or before.source not in (LA.SELLER, LA.SHOP)
        rows.append(BulkRow(content_id=content.id, label=label, before=before.values, after=after, changed=changed, note=note))
        if body.apply and changed:
            _set(content, shop, props, prop.name, ValueIn(values=after, unit=unit))
    changed = sum(1 for r in rows if r.changed)
    skipped = sum(1 for r in rows if r.reason)
    where = f" in {shop_label(shop)}" if shop else ""
    value = ", ".join(body.values) or "not set"
    if body.apply and changed:
        audit.record(session, "listing.attributes_changed", actor=tenant, target_tenant_id=tenant.id, listings=changed,
                     connection_id=str(shop.id) if shop else None, names=[body.name])
        await session.commit()
    sentence = (f"{body.name} = {value}{where}: {changed} listing{'' if changed == 1 else 's'} change"
                + (f", {skipped} skipped" if skipped else "") + "."
                + (" Saved." if body.apply and changed else " Nothing is saved until you apply it."))
    return BulkOut(rows=rows, changed=changed, skipped=skipped, applied=body.apply and changed > 0, sentence=sentence)

