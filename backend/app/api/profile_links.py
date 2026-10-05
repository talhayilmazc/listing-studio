"""One profile across shops (v8 §C): "Use in", the setup of each shop, and
"Link these".

``POST /api/profiles/{id}/use-in``       All shops / a group / chosen shops: a link
                                         per new shop, then one job reads them all,
                                         links every exact match at once and keeps
                                         what is left for the seller.
``GET  /api/profiles/{id}/setup``        per shop: linked, or what is missing, why,
                                         what could be created (with its requests)
                                         and what to choose from.
``POST /api/profiles/{id}/setup/create`` creates only what the seller confirmed.
``PUT  /api/profiles/{id}/setup/{shop}`` the seller picks one of that shop's own.
``DELETE /api/profiles/{id}/links/{shop}`` stop using the profile in a shop.
``GET  /api/profiles/link-suggestions``  same-named profiles that stayed apart.
``POST /api/profiles/{id}/link-profile`` "Link these": the other becomes this one's.
``PUT  /api/profiles/{id}/reference``    a reference in the main shop (after its
                                         old main shop was disconnected).

Every endpoint works only on the caller's own profiles and shops (404 otherwise).
Nothing is ever created in a shop without the request that confirms it.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.api.profiles import _get, _out, shop_names
from app.core import audit
from app.db.models import ListingProfile, ProfileShopLink, ShopGroup, Tenant
from app.etsy.shops import active_shops, owned_shop
from app.pipeline import links as L
from app.pipeline import profile_shops
from app.workers.links import PLAN_KEY

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

_NOT_FOUND = HTTPException(status_code=404, detail="profile not found")


class UseIn(BaseModel):
    scope: Literal["all", "group", "shops"]
    group_id: uuid.UUID | None = None
    connection_ids: list[uuid.UUID] = Field(default_factory=list)


class UseInOut(BaseModel):
    profile: schemas.ProfileOut
    #: Shops being set up now (already-linked shops are left as they are).
    started: list[uuid.UUID]


class Choice(BaseModel):
    id: int
    label: str


class ResourceSetup(BaseModel):
    resource: str
    label: str
    reason: str | None
    creatable: bool = False
    #: What creating it would make, in the seller's words, and its Etsy requests.
    create_summary: str | None = None
    requests: int = 0
    options: list[Choice] = Field(default_factory=list)


class ShopSetup(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None
    link: schemas.ProfileLinkOut
    open: list[ResourceSetup] = Field(default_factory=list)
    #: The choices are read from Etsy and kept a few hours; past that, check again.
    choices_expired: bool = False


class SetupOut(BaseModel):
    profile: schemas.ProfileOut
    shops: list[ShopSetup]


class CreateItem(BaseModel):
    connection_id: uuid.UUID
    resource: Literal["shipping_profile", "return_policy", "readiness_state"]


class CreateIn(BaseModel):
    items: list[CreateItem] = Field(min_length=1)


class CreateOut(BaseModel):
    profile: schemas.ProfileOut
    #: Etsy requests the confirmed creations take (counted against your daily Etsy requests).
    requests: int


class PickIn(BaseModel):
    resource: Literal["shipping_profile", "return_policy", "readiness_state", "production_partners"]
    #: One id, or several for production partners.
    ids: list[int] = Field(min_length=1)


class ReferenceIn(BaseModel):
    reference_listing_id: int


class LinkProfileIn(BaseModel):
    other_profile_id: uuid.UUID


class Suggestion(BaseModel):
    name: str
    profiles: list[schemas.ProfileOut]
    why: str


def _summary(resource: str, create: dict[str, Any] | None) -> str | None:
    if not create:
        return None
    if resource == L.SHIPPING:
        prof = create.get("profile") or {}
        dests = 1 + len(create.get("destinations") or [])
        ups = len(create.get("upgrades") or [])
        extra = f", {ups} upgrade{'s' if ups != 1 else ''}" if ups else ""
        return f'Shipping profile "{prof.get("title")}" with {dests} destination{"s" if dests != 1 else ""}{extra}'
    if resource == L.RETURNS:
        return "Return policy: " + L.label_of(L.RETURNS, create)
    if resource == L.READINESS:
        return "Processing profile: " + L.label_of(L.READINESS, {
            "readiness_state": create.get("readiness_state"),
            "min_processing_days": create.get("min_processing_time"),
            "max_processing_days": create.get("max_processing_time"),
        })
    return None


async def _plan(redis: Any, profile_id: uuid.UUID) -> dict[str, Any]:
    import json

    raw = await redis.get(PLAN_KEY.format(profile_id=profile_id))
    return json.loads(raw) if raw else {}


async def _save_plan(redis: Any, profile_id: uuid.UUID, plan: dict[str, Any]) -> None:
    import json

    from app.workers.links import PLAN_TTL_SECONDS

    await redis.set(PLAN_KEY.format(profile_id=profile_id), json.dumps(plan), ex=PLAN_TTL_SECONDS)


async def _scope_shops(session: AsyncSession, tenant: Tenant, body: UseIn) -> list[uuid.UUID]:
    shops = await active_shops(session, tenant.id)
    if body.scope == "all":
        return [c.id for c in shops]
    if body.scope == "group":
        group = await session.get(ShopGroup, body.group_id) if body.group_id else None
        if group is None or group.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="group not found")
        return [c.id for c in shops if c.group_id == group.id]
    mine = {c.id for c in shops}
    if not body.connection_ids or not set(body.connection_ids) <= mine:
        raise HTTPException(status_code=404, detail="shop not found")
    return list(dict.fromkeys(body.connection_ids))


@router.post("/{profile_id}/use-in", response_model=UseInOut)
async def use_in(
    profile_id: uuid.UUID,
    body: UseIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> UseInOut:
    """Set the profile up in every shop of the selection, in one go."""
    profile = await _get(session, tenant, profile_id)
    if not profile.confirmed:
        raise HTTPException(status_code=409, detail="confirm the profile first")
    targets = await _scope_shops(session, tenant, body)
    links = {link.connection_id: link for link in (await profile_shops.links_of(session, [profile.id]))[profile.id]}
    started: list[uuid.UUID] = []
    for shop_id in targets:
        if shop_id == profile.connection_id:
            continue
        link = links.get(shop_id)
        if link is not None and link.status not in ("error",):
            continue  # linked or being linked: left as it is
        if link is None:
            link = ProfileShopLink(tenant_id=tenant.id, profile_id=profile.id, connection_id=shop_id)
            session.add(link)
        link.status, link.notes, link.pending_create = "checking", None, None
        started.append(shop_id)
    await profile_shops.ensure_main_link(session, profile)
    await session.commit()
    if started:
        await enqueuer.enqueue("link_profile", str(profile.id))
    return UseInOut(profile=await _out(session, tenant, profile), started=started)


@router.get("/{profile_id}/setup", response_model=SetupOut)
async def setup(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    redis: Any = Depends(get_redis),
) -> SetupOut:
    profile = await _get(session, tenant, profile_id)
    out = await _out(session, tenant, profile)
    plan = await _plan(redis, profile.id)
    names = await shop_names(session, tenant)
    shops: list[ShopSetup] = []
    for link in out.links:
        if link.main:
            continue
        left = plan.get(str(link.connection_id))
        open_items: list[ResourceSetup] = []
        for miss in link.missing:
            entry = (left or {}).get(miss.resource) or {}
            open_items.append(ResourceSetup(
                resource=miss.resource, label=miss.label, reason=miss.reason,
                creatable=bool(entry.get("creatable")), requests=int(entry.get("requests") or 0),
                create_summary=_summary(miss.resource, entry.get("create")),
                options=[Choice(**o) for o in entry.get("options") or [] if o.get("id") is not None],
            ))
        shops.append(ShopSetup(
            connection_id=link.connection_id, shop_name=names.get(link.connection_id), link=link, open=open_items,
            choices_expired=bool(link.missing) and left is None,
        ))
    return SetupOut(profile=out, shops=shops)


async def _link(session: AsyncSession, profile: ListingProfile, shop_id: uuid.UUID) -> ProfileShopLink:
    link = await profile_shops.link_for(session, profile, shop_id)
    if link is None or shop_id == profile.connection_id:
        raise HTTPException(status_code=404, detail="the profile is not set up in that shop")
    return link


@router.post("/{profile_id}/setup/create", response_model=CreateOut)
async def create_in_shops(
    profile_id: uuid.UUID,
    body: CreateIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Any = Depends(get_redis),
) -> CreateOut:
    """Create in each shop exactly what the seller confirmed, copied from the main shop."""
    profile = await _get(session, tenant, profile_id)
    plan = await _plan(redis, profile.id)
    requests = 0
    by_link: dict[uuid.UUID, set[str]] = {}
    for item in body.items:
        if await owned_shop(session, tenant.id, item.connection_id) is None:
            raise HTTPException(status_code=404, detail="shop not found")
        await _link(session, profile, item.connection_id)
        entry = (plan.get(str(item.connection_id)) or {}).get(item.resource)
        if not entry or not entry.get("creatable"):
            raise HTTPException(
                status_code=409,
                detail=f"{L.LABEL[item.resource]} cannot be created in that shop now; check the shop again first",
            )
        requests += int(entry.get("requests") or 0)
        by_link.setdefault(item.connection_id, set()).add(item.resource)
    for shop_id, resources in by_link.items():
        link = await _link(session, profile, shop_id)
        link.pending_create = sorted(resources)
        link.status = "checking"
    await session.commit()
    await enqueuer.enqueue("create_link_resources", str(profile.id))
    return CreateOut(profile=await _out(session, tenant, profile), requests=requests)


@router.put("/{profile_id}/setup/{connection_id}", response_model=schemas.ProfileOut)
async def pick_in_shop(
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
    body: PickIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    redis: Any = Depends(get_redis),
) -> schemas.ProfileOut:
    """The seller picks one of the shop's own settings (only ids Etsy listed for it)."""
    profile = await _get(session, tenant, profile_id)
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    link = await _link(session, profile, connection_id)
    plan = await _plan(redis, profile.id)
    shop_plan = plan.get(str(connection_id)) or {}
    entry = shop_plan.get(body.resource)
    allowed = {int(o["id"]) for o in (entry or {}).get("options") or [] if o.get("id") is not None}
    if not entry or not set(body.ids) <= allowed:
        raise HTTPException(status_code=409, detail="choose from the shop's current list; check the shop again first")
    if body.resource != L.PARTNERS and len(body.ids) != 1:
        raise HTTPException(status_code=422, detail="choose one")
    value: Any = sorted(body.ids) if body.resource == L.PARTNERS else body.ids[0]
    setattr(link, L.PAYLOAD_KEY[body.resource], value)
    notes = dict(link.notes or {})
    notes.pop(body.resource, None)
    link.notes = notes or None
    link.status = "incomplete" if notes else "ready"
    shop_plan.pop(body.resource, None)
    plan[str(connection_id)] = shop_plan
    await session.commit()
    await _save_plan(redis, profile.id, plan)
    return await _out(session, tenant, profile)


@router.post("/{profile_id}/setup/{connection_id}/check", response_model=schemas.ProfileOut)
async def check_shop_again(
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    """Read the shop's settings again (choices expired, or the seller changed them in Shop Manager)."""
    profile = await _get(session, tenant, profile_id)
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    link = await _link(session, profile, connection_id)
    link.status, link.pending_create = "checking", None
    await session.commit()
    await enqueuer.enqueue("link_profile", str(profile.id))
    return await _out(session, tenant, profile)


@router.delete("/{profile_id}/links/{connection_id}", response_model=schemas.ProfileOut)
async def stop_using_in_shop(
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ProfileOut:
    profile = await _get(session, tenant, profile_id)
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    if connection_id == profile.connection_id:
        raise HTTPException(status_code=409, detail="this is the profile's main shop; its reference listing is there")
    link = await _link(session, profile, connection_id)
    audit.destructive(session, "profile.unlinked", actor=tenant, tenant_id=tenant.id, shop_id=connection_id,
                      object_id=profile.id)
    await session.delete(link)
    await session.commit()
    return await _out(session, tenant, profile)


@router.put("/{profile_id}/reference", response_model=schemas.ProfileOut)
async def set_reference(
    profile_id: uuid.UUID,
    body: ReferenceIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    """A reference listing in the main shop (one of that shop's own listings)."""
    profile = await _get(session, tenant, profile_id)
    profile.reference_listing_id = body.reference_listing_id
    profile.refresh_error = profile.refresh_failed_at = None
    await session.commit()
    await enqueuer.enqueue("refresh_profile", str(profile.id))
    return await _out(session, tenant, profile)


# --- same-named profiles: "Link these" ----------------------------------------------------------


def _costs(tenant: Tenant, profile_id: uuid.UUID) -> Any:
    return ((tenant.cost_settings or {}).get("profile_costs") or {}).get(str(profile_id))


@router.get("/link-suggestions", response_model=list[Suggestion])
async def link_suggestions(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[Suggestion]:
    """Profiles with the same name whose main shops differ: one profile could serve them."""
    rows = list((await session.execute(
        select(ListingProfile).where(ListingProfile.tenant_id == tenant.id).order_by(ListingProfile.created_at)
    )).scalars())
    groups: dict[str, list[ListingProfile]] = {}
    for p in rows:
        groups.setdefault(p.name.strip().casefold(), []).append(p)
    out: list[Suggestion] = []
    for members in groups.values():
        if len({p.connection_id for p in members}) < 2:
            continue
        signatures = {repr(L.shared_signature(p, _costs(tenant, p.id))) for p in members}
        same = len(signatures) == 1 and "None" not in signatures
        why = ("their shared settings are the same" if same else
               "their shared settings differ (category, prices, variations, description, prefix, costs or size charts); "
               "linking keeps the first one's")
        out.append(Suggestion(name=members[0].name, profiles=[await _out(session, tenant, p) for p in members], why=why))
    return out


@router.post("/{profile_id}/link-profile", response_model=schemas.ProfileOut)
async def link_profiles(
    profile_id: uuid.UUID,
    body: LinkProfileIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    """"Link these": the other profile's shop becomes one of this profile's; the
    other profile goes, and everything that named it names this one."""
    keep = await _get(session, tenant, profile_id)
    other = await _get(session, tenant, body.other_profile_id)
    if keep.id == other.id:
        raise HTTPException(status_code=422, detail="choose another profile")
    if other.connection_id == keep.connection_id:
        raise HTTPException(status_code=409, detail="both profiles are in the same main shop")
    audit.destructive(session, "profile.merged", actor=tenant, tenant_id=tenant.id, shop_id=other.connection_id,
                      object_id=other.id, kept_profile_id=str(keep.id))
    settings = dict(tenant.cost_settings or {})
    costs = dict(settings.get("profile_costs") or {})
    if costs.pop(str(other.id), None) is not None:
        settings["profile_costs"] = costs
        tenant.cost_settings = settings
    result = await profile_shops.absorb(session, keep, other)
    await session.commit()
    if result["links"]:
        await enqueuer.enqueue("link_profile", str(keep.id))  # a link from a lapsed reference is checked
    return await _out(session, tenant, keep)
