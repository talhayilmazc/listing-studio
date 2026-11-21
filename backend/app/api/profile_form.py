"""Profiles made and edited by hand (feature/manual-profiles; pipeline/profile_values.py).

``GET  /api/profile-form/options?shop=``                 the shop's own shipping profiles, return policies, processing
                                                         profiles and production partners, and its currency
``GET  /api/profile-form/categories?shop=&q=``            Etsy's categories whose name has every word of ``q``
``GET  /api/profile-form/category?shop=&taxonomy_id=``    a category's attributes and its name
``GET  /api/profile-form/copy?profile=&shop=``            another profile's values for a new one in ``shop``: the
                                                         shop's own settings matched by the rules of every setup
                                                         (pipeline/links.py); what does not match is asked
``POST /api/profile-form``                                a new profile from the form: no reference listing
``GET  /api/profiles/{id}/values``                        a profile's values for the form, and which are the seller's
``PUT  /api/profiles/{id}/values``                        save: on a profile that follows a listing, what differs from
                                                         the listing becomes "your value"
``DELETE /api/profiles/{id}/values?field=``               "Use the listing's value again"
``POST /api/profiles/{id}/stop-following``                the profile keeps its values and stops following its listing

Nothing here calls Etsy: what the form offers was read through the queue (``read_profile_options``) and is asked
for again when it is not there (the answer says ``reading``). Only values Etsy would take are saved; each refusal
is one plain sentence about one field. The caller's own shops and profiles only (404).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.api.profile_links import _shop_lists
from app.api.profiles import _get, _out
from app.api.shops import shop_label
from app.core import audit
from app.db.models import EtsyConnection, ListingProfile, Tenant
from app.etsy.shops import owned_shop
from app.pipeline import links as L
from app.pipeline import listing_attributes, profile_shops
from app.pipeline import profile_values as V
from app.pipeline.taxonomy import category_path, clothing_taxonomy_ids, infer_content_template

router = APIRouter(prefix="/api", tags=["profile-form"])

NODES_KEY = "etsy:taxonomy:nodes"
PROPS_KEY = "etsy:taxonomy:props:{taxonomy_id}"
MAX_CATEGORIES = 40
STYLES = ("search", "classic")


class Option(BaseModel):
    id: int
    label: str
    #: A shipping profile Etsy calculates from the parcel ("calculated"): the form then asks the weight and size.
    type: str | None = None


class OptionsOut(BaseModel):
    shop_id: uuid.UUID
    shop_name: str
    #: The lists are being read from Etsy: ask again in a moment.
    reading: bool = False
    currency: str | None = None
    shipping: list[Option] = Field(default_factory=list)
    returns: list[Option] = Field(default_factory=list)
    readiness: list[Option] = Field(default_factory=list)
    partners: list[Option] = Field(default_factory=list)
    #: Said under the production partner when the shop has none on Etsy (drafts are made without one).
    partner_note: str | None = None
    who_made: list[dict[str, str]] = Field(default_factory=list)
    when_made: list[dict[str, str]] = Field(default_factory=list)


class CategoryOut(BaseModel):
    id: int
    path: list[str]


class CategoriesOut(BaseModel):
    reading: bool = False
    categories: list[CategoryOut] = Field(default_factory=list)


class CategoryDetailOut(BaseModel):
    reading: bool = False
    taxonomy_id: int
    path: list[str] = Field(default_factory=list)
    #: The attributes the category offers (pipeline/listing_attributes.compact).
    properties: list[dict[str, Any]] = Field(default_factory=list)


class FormIn(BaseModel):
    name: str
    values: dict[str, Any] = Field(default_factory=dict)
    listing_style: str | None = None
    title_prefix: str | None = None
    default_tags: list[str] | None = None
    personalization: dict[str, Any] | None = None


class CreateIn(FormIn):
    connection_id: uuid.UUID
    #: The profile these values were copied from (said in the audit log only).
    copied_from: uuid.UUID | None = None


class Asked(BaseModel):
    field: str
    label: str
    #: The main shop's own setting, by name, and why it has no counterpart here.
    source: str | None = None
    reason: str


class ValuesOut(BaseModel):
    profile_id: uuid.UUID | None = None
    connection_id: uuid.UUID
    shop_name: str
    name: str
    manual: bool
    #: The payload is not read yet (a profile that follows a listing, after 24 hours): ask again in a moment.
    reading: bool = False
    values: dict[str, Any] = Field(default_factory=dict)
    #: The fields that are the seller's own ("your value"), and the listing's value of each.
    own_fields: list[str] = Field(default_factory=list)
    listing_values: dict[str, Any] = Field(default_factory=dict)
    listing_style: str = "search"
    title_prefix: str | None = None
    default_tags: list[str] = Field(default_factory=list)
    personalization: dict[str, Any] | None = None
    category_path: list[str] = Field(default_factory=list)
    #: For a copy: what the target shop has no counterpart for, asked in the form; and notes (currency, size charts).
    asked: list[Asked] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    partner_note: str | None = None
    labels: dict[str, str] = Field(default_factory=lambda: dict(V.LABELS))
    groups: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in V.GROUPS.items()})


class Refused(BaseModel):
    """What Etsy would refuse, by field: nothing was saved."""

    message: str
    fields: dict[str, str]


# --- helpers ------------------------------------------------------------------------------------


async def _shop(session: AsyncSession, tenant: Tenant, shop_id: uuid.UUID) -> EtsyConnection:
    shop = await owned_shop(session, tenant.id, shop_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    return shop


def _options_of(lists: L.ShopSettings) -> dict[str, list[dict[str, Any]]]:
    types = {L.id_of(L.SHIPPING, row): row.get("profile_type") for row in lists.shipping}
    return {
        "shipping": [{**o, "type": types.get(o["id"])} for o in lists.options(L.SHIPPING)],
        "returns": lists.options(L.RETURNS), "readiness": lists.options(L.READINESS), "partners": lists.options(L.PARTNERS),
    }


async def _lists(redis: Any, enqueuer: Enqueuer, shop: EtsyConnection, taxonomy_id: int | None = None) -> L.ShopSettings | None:
    """The shop's lists as last read; None while they are being read (asked for through the queue)."""
    lists = await _shop_lists(redis, shop.id)
    if lists is None or lists.currency is None:
        await enqueuer.enqueue("read_profile_options", f"{shop.id}|{taxonomy_id}" if taxonomy_id else str(shop.id))
        return None
    return lists


async def _cached(redis: Any, key: str) -> Any:
    raw = await redis.get(key)
    return json.loads(raw) if raw else None


def _walk(nodes: Any, above: tuple[str, ...] = ()):  # noqa: ANN202
    for node in (nodes.get("results", []) if isinstance(nodes, dict) else nodes or []):
        name = str(node.get("name") or "").strip()
        path = (*above, name) if name else above
        if node.get("id") is not None:
            yield int(node["id"]), path, not node.get("children")
        yield from _walk(node.get("children") or [], path)


def _refuse(fields: dict[str, str]) -> None:
    if fields:
        first = next(iter(fields.values()))
        more = f" ({len(fields) - 1} more)" if len(fields) > 1 else ""
        raise HTTPException(status_code=422, detail={"message": f"Not saved: {first}{more}", "fields": fields})


def _clean(values: dict[str, Any], options: dict[str, Any] | None) -> dict[str, Any]:
    """Only the form's own fields, in the form they are stored; the shipping profile's kind beside it."""
    out = {k: values[k] for k in V.PAYLOAD_FIELDS if k in values}
    if "variations" in out:
        out["variations"] = V.clean_variations(out["variations"])
    if "materials" in out:
        out["materials"] = [str(m).strip() for m in out["materials"] or [] if str(m).strip()]
    if "production_partner_ids" in out:
        out["production_partner_ids"] = [int(i) for i in out["production_partner_ids"] or []]
    for key in ("taxonomy_id", "shipping_profile_id", "return_policy_id", "readiness_state_id", "quantity"):
        if out.get(key) not in (None, ""):
            try:
                out[key] = int(out[key])
            except (TypeError, ValueError):
                pass
        elif key in out:
            out[key] = None
    if "shipping_profile_id" in out and options is not None:
        kind = next((o.get("type") for o in options.get("shipping") or [] if o["id"] == out["shipping_profile_id"]), None)
        out["shipping_profile_type"] = kind
    return out


def _text_settings(profile: ListingProfile, body: FormIn) -> dict[str, str]:
    """The profile's own text settings from the form (checked, then set)."""
    found = V.text_problems(title_prefix=body.title_prefix, default_tags=body.default_tags, personalization=body.personalization)
    if body.listing_style is not None and body.listing_style not in STYLES:
        found["listing_style"] = "Choose a title style: short and clear, or long keyword."
    if found:
        return found
    if body.listing_style is not None:
        profile.listing_style = body.listing_style
    if body.title_prefix is not None:
        profile.title_prefix = body.title_prefix.strip()
    if body.default_tags is not None:
        profile.default_tags = V.clean_tags(body.default_tags) or None
    if body.personalization is not None:
        from app.pipeline import personalization as P

        profile.personalization = P.validate(body.personalization) if body.personalization.get("enabled") else {"enabled": False}
    return {}


async def _category(redis: Any, taxonomy_id: Any) -> tuple[list[str], list[dict[str, Any]] | None]:
    if taxonomy_id in (None, ""):
        return [], None
    nodes = await _cached(redis, NODES_KEY)
    props = await _cached(redis, PROPS_KEY.format(taxonomy_id=int(taxonomy_id)))
    return (category_path(nodes, int(taxonomy_id)) if nodes else [],
            listing_attributes.compact(props.get("results")) if props else None)


async def _relink(session: AsyncSession, profile: ListingProfile, enqueuer: Enqueuer, before: dict[str, Any]) -> None:
    """The main shop's shipping, return, processing or partner changed on a profile used in other shops: each
    other shop is matched to the new one again (the same rules as every setup)."""
    after = profile.cached_payload or {}
    if all(before.get(k) == after.get(k) for k in L.PAYLOAD_KEY.values()):
        return
    changed = False
    for link in (await profile_shops.links_of(session, [profile.id]))[profile.id]:
        if link.connection_id != profile.connection_id and link.status != "checking":
            link.status, link.pending_create = "checking", [L.COPY_ALL]
            changed = True
    if changed:
        await session.flush()
        await enqueuer.enqueue("link_profile", str(profile.id))


# --- what the form offers -----------------------------------------------------------------------


@router.get("/profile-form/options", response_model=OptionsOut)
async def form_options(shop: uuid.UUID = Query(...), session: AsyncSession = Depends(get_session, scope="function"),
                       tenant: Tenant = Depends(active_tenant), redis: Any = Depends(get_redis),
                       enqueuer: Enqueuer = Depends(get_enqueuer)) -> OptionsOut:
    connection = await _shop(session, tenant, shop)
    name = shop_label(connection)
    words = {"who_made": [{"value": k, "label": V.WHO_WORDS[k]} for k in V.WHO_MADE],
             "when_made": [{"value": k, "label": V.WHEN_WORDS.get(k, k.replace("_", " "))} for k in V.WHEN_MADE]}
    lists = await _lists(redis, enqueuer, connection)
    if lists is None:
        return OptionsOut(shop_id=connection.id, shop_name=name, reading=True, **words)
    options = _options_of(lists)
    return OptionsOut(shop_id=connection.id, shop_name=name, currency=lists.currency, **options, **words,
                      partner_note=None if options["partners"] else L.partner_note(name, L.NO_PARTNER).replace(
                          ", then press Refresh.", "."))


@router.get("/profile-form/categories", response_model=CategoriesOut)
async def form_categories(shop: uuid.UUID = Query(...), q: str = Query("", max_length=80),
                          session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                          redis: Any = Depends(get_redis), enqueuer: Enqueuer = Depends(get_enqueuer)) -> CategoriesOut:
    connection = await _shop(session, tenant, shop)
    nodes = await _cached(redis, NODES_KEY)
    if nodes is None:
        await enqueuer.enqueue("read_profile_options", str(connection.id))
        return CategoriesOut(reading=True)
    words = [w for w in q.lower().split() if w]
    found: list[CategoryOut] = []
    if words:
        for node_id, path, leaf in _walk(nodes):
            text = " ".join(path).lower()
            if all(w in text for w in words):
                found.append(CategoryOut(id=node_id, path=list(path)))
        # The most specific first: a category named by the words themselves before the ones above it.
        found.sort(key=lambda c: (not all(w in c.path[-1].lower() for w in words), len(c.path), c.path))
    return CategoriesOut(categories=found[:MAX_CATEGORIES])


@router.get("/profile-form/category", response_model=CategoryDetailOut)
async def form_category(shop: uuid.UUID = Query(...), taxonomy_id: int = Query(..., ge=1),
                        session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                        redis: Any = Depends(get_redis), enqueuer: Enqueuer = Depends(get_enqueuer)) -> CategoryDetailOut:
    connection = await _shop(session, tenant, shop)
    path, props = await _category(redis, taxonomy_id)
    if props is None:
        await enqueuer.enqueue("read_profile_options", f"{connection.id}|{taxonomy_id}")
        return CategoryDetailOut(reading=True, taxonomy_id=taxonomy_id, path=path)
    return CategoryDetailOut(taxonomy_id=taxonomy_id, path=path, properties=props)


# --- a profile's values -------------------------------------------------------------------------


async def _values_out(session: AsyncSession, tenant: Tenant, profile: ListingProfile, redis: Any) -> ValuesOut:
    shop = await session.get(EtsyConnection, profile.connection_id)
    payload = profile.cached_payload
    out = ValuesOut(
        profile_id=profile.id, connection_id=profile.connection_id, shop_name=shop_label(shop) if shop else "",
        name=profile.name, manual=bool(profile.manual), listing_style=profile.listing_style,
        title_prefix=profile.title_prefix, default_tags=list(profile.default_tags or []),
        personalization=profile.personalization, own_fields=V.own_fields(profile),
    )
    if profile.manual:
        out.values = {**V.from_payload(V.to_payload(profile.own_values or {}, currency=None)),
                      **{k: v for k, v in (profile.own_values or {}).items() if k in V.PAYLOAD_FIELDS}}
    elif payload:
        out.values = V.from_payload(payload)
        out.listing_values = dict(payload.get("_listing") or {})
    else:
        out.reading = True
    out.category_path = list((payload or {}).get("category_names") or []) or (await _category(redis, out.values.get("taxonomy_id")))[0]
    return out


@router.get("/profiles/{profile_id}/values", response_model=ValuesOut)
async def profile_values(profile_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"),
                         tenant: Tenant = Depends(active_tenant), redis: Any = Depends(get_redis),
                         enqueuer: Enqueuer = Depends(get_enqueuer)) -> ValuesOut:
    profile = await _get(session, tenant, profile_id)
    out = await _values_out(session, tenant, profile, redis)
    if out.reading or (profile.manual and "category_properties" not in (profile.cached_payload or {})):
        # Its listing (or, for a profile made by hand, its category's lists) is read again for the form.
        await enqueuer.enqueue("refresh_profile", str(profile.id))
    return out


@router.post("/profile-form", response_model=schemas.ProfileOut, status_code=201,
             responses={422: {"model": Refused}})
async def create_from_form(body: CreateIn, session: AsyncSession = Depends(get_session, scope="function"),
                           tenant: Tenant = Depends(active_tenant), redis: Any = Depends(get_redis),
                           enqueuer: Enqueuer = Depends(get_enqueuer)) -> schemas.ProfileOut:
    """A profile made by hand, for one shop: no reference listing; every value is the seller's own."""
    shop = await _shop(session, tenant, body.connection_id)
    lists = await _shop_lists(redis, shop.id)
    if lists is None:
        await enqueuer.enqueue("read_profile_options", str(shop.id))
        raise HTTPException(status_code=409, detail=f"{shop_label(shop)}'s settings are being read from Etsy. Try again in a moment.")
    options = _options_of(lists)
    values = _clean(body.values, options)
    path, props = await _category(redis, values.get("taxonomy_id"))
    profile = ListingProfile(tenant_id=tenant.id, connection_id=shop.id, name=body.name.strip(), reference_listing_id=None,
                             manual=True, source="manual", confirmed=True, content_template="apparel")
    fields = {**V.problems(values, options=options, name=body.name, category_properties=props), **_text_settings(profile, body)}
    _refuse(fields)
    nodes = await _cached(redis, NODES_KEY)
    if nodes:
        profile.content_template = infer_content_template(values.get("taxonomy_id"), clothing_taxonomy_ids(nodes))
    profile.own_values = values
    if profile.title_prefix is None:
        profile.title_prefix = ""  # deliberately none: there is no reference title to derive one from
    V.rebuild(profile, currency=lists.currency)
    if path or props is not None:
        from datetime import datetime, timezone

        profile.cached_payload = {**profile.cached_payload, "category_names": path,
                                  **({"category_properties": props, "category_read_at": datetime.now(timezone.utc).isoformat()}
                                     if props is not None else {})}
    session.add(profile)
    await session.flush()
    await profile_shops.ensure_main_link(session, profile)
    audit.record(session, "profile.created_by_hand", actor=tenant, target_tenant_id=tenant.id, profile_id=str(profile.id),
                 shop_id=str(shop.id), copied_from=str(body.copied_from) if body.copied_from else None)
    await session.commit()
    await session.refresh(profile)
    # Its category's attribute lists, for the review card (no listing is read: there is none).
    await enqueuer.enqueue("refresh_profile", str(profile.id))
    return await _out(session, tenant, profile)


@router.put("/profiles/{profile_id}/values", response_model=schemas.ProfileOut, responses={422: {"model": Refused}})
async def save_values(profile_id: uuid.UUID, body: FormIn, session: AsyncSession = Depends(get_session, scope="function"),
                      tenant: Tenant = Depends(active_tenant), redis: Any = Depends(get_redis),
                      enqueuer: Enqueuer = Depends(get_enqueuer)) -> schemas.ProfileOut:
    """Save the form. A manual profile: its values. A profile that follows a listing: what differs from the
    listing's own becomes "your value" (a Refresh never overwrites it); the rest keeps following the listing."""
    profile = await _get(session, tenant, profile_id)
    shop = await _shop(session, tenant, profile.connection_id)
    lists = await _shop_lists(redis, shop.id)
    options = _options_of(lists) if lists is not None else None
    values = _clean(body.values, options)
    before = dict(profile.cached_payload or {})
    taxonomy = values.get("taxonomy_id", before.get("taxonomy_id"))
    _, props = await _category(redis, taxonomy)
    if not profile.manual and not profile.cached_payload:
        await enqueuer.enqueue("refresh_profile", str(profile.id))
        raise HTTPException(status_code=409, detail="The profile's listing is being read again. Try again in a moment.")
    if profile.manual:
        merged = {**(profile.own_values or {}), **values}
        fields = V.problems(merged, options=options, name=body.name, category_properties=props)
    else:
        # Only what the seller changes is theirs to answer for: the rest is the listing's own, as Etsy has it.
        listing = V.from_payload(V.base_of(profile.cached_payload))
        changed = {k: v for k, v in values.items() if k in V.PAYLOAD_FIELDS and not V._equal(k, v, listing.get(k))}
        found = V.problems({**V.from_payload(profile.cached_payload), **changed}, options=options, name=body.name,
                           category_properties=props)
        fields = {k: v for k, v in found.items() if k in changed or k == "name"}
    fields.update(_text_settings(profile, body))
    _refuse(fields)
    profile.name = body.name.strip()
    if profile.manual:
        profile.own_values = merged
        V.rebuild(profile, currency=(lists.currency if lists else None) or before.get("currency"))
    else:
        V.set_own(profile, values)
        V.rebuild(profile)
    audit.record(session, "profile.values_changed", actor=tenant, target_tenant_id=tenant.id, profile_id=str(profile.id),
                 fields=sorted(values), own=V.own_fields(profile) if not profile.manual else None)
    await _relink(session, profile, enqueuer, before)
    await session.commit()
    await session.refresh(profile)
    if profile.manual and before.get("taxonomy_id") != (profile.cached_payload or {}).get("taxonomy_id"):
        await enqueuer.enqueue("refresh_profile", str(profile.id))
    return await _out(session, tenant, profile)


@router.delete("/profiles/{profile_id}/values", response_model=schemas.ProfileOut)
async def use_listing_value(profile_id: uuid.UUID, field: str = Query(...),
                            session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                            enqueuer: Enqueuer = Depends(get_enqueuer)) -> schemas.ProfileOut:
    """"Use the listing's value again": the field follows the reference listing from now on."""
    profile = await _get(session, tenant, profile_id)
    if profile.manual:
        raise HTTPException(status_code=409, detail="This profile does not follow a listing: its values are its own.")
    if field not in (profile.own_values or {}):
        return await _out(session, tenant, profile)
    before = dict(profile.cached_payload or {})
    own = {k: v for k, v in (profile.own_values or {}).items() if k != field}
    profile.own_values = own or None
    V.rebuild(profile)
    audit.record(session, "profile.values_changed", actor=tenant, target_tenant_id=tenant.id, profile_id=str(profile.id),
                 fields=[field], own=V.own_fields(profile), back_to_listing=True)
    await _relink(session, profile, enqueuer, before)
    await session.commit()
    await session.refresh(profile)
    return await _out(session, tenant, profile)


@router.post("/profiles/{profile_id}/stop-following", response_model=schemas.ProfileOut)
async def stop_following(profile_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"),
                         tenant: Tenant = Depends(active_tenant), enqueuer: Enqueuer = Depends(get_enqueuer)) -> schemas.ProfileOut:
    """The profile keeps every value it has now as the seller's own and stops following its reference listing:
    it is never refreshed from the listing again. (Its size chart images were the listing's: they are no longer
    added to drafts.)"""
    profile = await _get(session, tenant, profile_id)
    if profile.manual:
        return await _out(session, tenant, profile)
    if not profile.cached_payload:
        await enqueuer.enqueue("refresh_profile", str(profile.id))
        raise HTTPException(status_code=409, detail="The profile's listing is being read once more to keep its values. Try again in a moment.")
    payload = dict(profile.cached_payload)
    values = V.from_payload(payload)
    values["shipping_profile_type"] = payload.get("shipping_profile_type")
    values["should_auto_renew"] = payload.get("should_auto_renew")
    if profile.personalization is None and payload.get("personalization"):
        from app.pipeline import personalization as P

        profile.personalization = P.effective(None, payload)
    listing_id = profile.reference_listing_id
    profile.manual, profile.own_values, profile.reference_listing_id = True, values, None
    profile.fixed_image_ids, profile.size_chart_slots = None, None
    profile.refresh_error = profile.refresh_failed_at = None
    V.rebuild(profile, currency=payload.get("currency"))
    audit.record(session, "profile.stopped_following", actor=tenant, target_tenant_id=tenant.id, profile_id=str(profile.id),
                 listing_id=listing_id)
    await session.commit()
    await session.refresh(profile)
    return await _out(session, tenant, profile)


# --- copy from another profile ------------------------------------------------------------------

_FIELD_OF = {L.SHIPPING: "shipping_profile_id", L.RETURNS: "return_policy_id", L.READINESS: "readiness_state_id",
             L.PARTNERS: "production_partner_ids"}


@router.get("/profile-form/copy", response_model=ValuesOut)
async def copy_form(profile: uuid.UUID = Query(...), shop: uuid.UUID = Query(...),
                    session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                    redis: Any = Depends(get_redis), enqueuer: Enqueuer = Depends(get_enqueuer)) -> ValuesOut:
    """Another profile's values for a new profile in ``shop``. The shop's own shipping profile, return policy,
    processing profile and production partner are matched by the rules of every setup (the profile's own link
    to that shop first, then the same name or the same terms); what has no counterpart is left empty and asked."""
    source = await _get(session, tenant, profile)
    target = await _shop(session, tenant, shop)
    name = shop_label(target)
    out = ValuesOut(connection_id=target.id, shop_name=name, name=f"{source.name} ({name})"[: V.MAX_NAME], manual=True,
                    listing_style=source.listing_style, title_prefix=source.title_prefix,
                    default_tags=list(source.default_tags or []), personalization=source.personalization)
    payload = source.cached_payload
    if not payload:
        await enqueuer.enqueue("refresh_profile", str(source.id))
        out.reading = True
        return out
    values = V.from_payload(payload)
    out.category_path = list(payload.get("category_names") or [])
    if out.personalization is None and payload.get("personalization"):
        from app.pipeline import personalization as P

        out.personalization = P.effective(None, payload)
    if target.id != source.connection_id:
        target_lists = await _lists(redis, enqueuer, target)
        main = await session.get(EtsyConnection, source.connection_id)
        source_lists = await _lists(redis, enqueuer, main) if main is not None else None
        if target_lists is None or source_lists is None:
            out.reading = True
            return out
        link = await profile_shops.link_for(session, source, target.id)
        linked = L.link_values(link) if link is not None else {}
        plan = L.plan_shop(payload, source_lists, target_lists)
        for resource, outcome in plan.items():
            field = _FIELD_OF[resource]
            if not outcome.needed:
                values[field] = [] if resource == L.PARTNERS else None
                continue
            value = linked.get(L.PAYLOAD_KEY[resource])
            known = {o["id"] for o in target_lists.options(resource)}
            ids = (value if isinstance(value, list) else [value]) if value not in (None, []) else []
            if ids and all(int(i) in known for i in ids):
                values[field] = value  # the shop's own, as this profile already uses it there
            elif outcome.linked not in (None, []):
                values[field] = outcome.linked
            else:
                values[field] = [] if resource == L.PARTNERS else None
                if resource == L.PARTNERS and outcome.reason == L.NO_PARTNER:
                    out.partner_note = L.partner_note(name, L.NO_PARTNER).replace(", then press Refresh.", ".")
                else:
                    out.asked.append(Asked(field=field, label=V.LABELS[field], source=outcome.source_label,
                                           reason=f"{name} has no {L.LABEL[resource].lower()} like "
                                                  f"{outcome.source_label or 'the main shop’s'}: choose one of {name}'s."))
        if payload.get("currency") and target_lists.currency and payload["currency"] != target_lists.currency:
            out.notes.append(f"{name} sells in {target_lists.currency}; the prices were copied as numbers from "
                             f"{payload['currency']}. Check them.")
    if source.fixed_image_ids:
        out.notes.append("Size chart images are the reference listing's own: they are not part of the copy.")
    out.values = values
    return out
