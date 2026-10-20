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
``POST /api/profiles/{id}/setup/apply``  every choice of a setup dialog in one confirm:
                                         picks of the shops' own settings, and copies
                                         of the main shop's (created only now).
``POST /api/profiles/{id}/setup/{shop}/check``  Refresh: read that shop's shipping,
                                         return and processing lists again (a setting
                                         the seller made by hand in Shop Manager can
                                         then be picked, or is matched by its name).
``GET  /api/profiles/shop/{shop}``       how many reference listings are set up in a shop,
                                         and which wait for the seller.
``POST /api/profiles/shop/{shop}/use-all``  "Use all reference listings here" (Shops page):
                                         every confirmed profile set up in that shop in one
                                         click (that click is the confirmation).
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
from sqlalchemy import false as sa_false
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.api.profiles import _get, _out, shop_names
from app.core import audit
from app.db.models import ListingProfile, ProfileShopLink, ShopGroup, Tenant
from app.etsy.shops import active_shops, group_shops, owned_shop
from app.pipeline import links as L
from app.pipeline import profile_shops, setting_map
from app.workers.links import PLAN_KEY, forget_lists

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


class SetupFailure(BaseModel):
    """Etsy refused (or did not answer) the copy of this setting: why, in plain words
    with Etsy's own reason, and the one action that fixes it (pipeline/links.py
    ``create_failure``): "reconnect", "fix" (``field`` names what to change on the main
    shop's setting), "finish" (made, but a part was refused), "refresh", "manual"."""

    reason: str
    action: Literal["reconnect", "fix", "finish", "refresh", "manual"]
    field: str | None = None


class SettingUse(BaseModel):
    """One shop-specific setting in one shop: what is used, or what will be."""

    resource: str
    label: str  # "Shipping profile"
    #: The main shop's own setting this stands for.
    source_label: str | None = None
    #: The shop's own setting in use now, and how it was chosen
    #: ("same name", "same terms", "your choice", "created"); empty = nothing yet.
    linked_ids: list[int] = Field(default_factory=list)
    linked_label: str | None = None
    how: str | None = None
    #: What the dialog preselects: "keep" what is linked, "copy" the main shop's
    #: setting into this shop (created only after the seller confirms), or
    #: "choose" (no match and no copy possible: the seller picks one).
    default: Literal["keep", "copy", "choose"] = "keep"
    reason: str | None = None
    creatable: bool = False
    create_summary: str | None = None
    requests: int = 0
    options: list[Choice] = Field(default_factory=list)
    #: The last copy of it was refused: the dialog says why, and preselects nothing.
    failure: SetupFailure | None = None


class ShopSetup(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None
    link: schemas.ProfileLinkOut
    open: list[ResourceSetup] = Field(default_factory=list)
    #: Every setting the profile needs in this shop: matched, chosen, or to settle.
    settings: list[SettingUse] = Field(default_factory=list)
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


class ApplyChoice(BaseModel):
    connection_id: uuid.UUID
    resource: Literal["shipping_profile", "return_policy", "readiness_state", "production_partners"]
    #: "pick": one of the shop's own (``ids``); "copy": create the main shop's there.
    use: Literal["pick", "copy"]
    ids: list[int] = Field(default_factory=list)


class ApplyIn(BaseModel):
    choices: list[ApplyChoice] = Field(min_length=1)


class PickIn(BaseModel):
    resource: Literal["shipping_profile", "return_policy", "readiness_state", "production_partners"]
    #: One id, or several for production partners.
    ids: list[int] = Field(min_length=1)


class ReferenceIn(BaseModel):
    reference_listing_id: int


class LinkProfileIn(BaseModel):
    other_profile_id: uuid.UUID


class ShopProfileNeed(BaseModel):
    id: uuid.UUID
    name: str
    reason: str | None = None


class ShopProfilesOut(BaseModel):
    """The account's reference listings in one shop (the Shops page's one button)."""

    connection_id: uuid.UUID
    #: Confirmed profiles whose reference listing is in another shop.
    total: int = 0
    set_up: int = 0
    checking: int = 0
    #: Never set up in this shop.
    not_set_up: int = 0
    #: Set up, but waiting for the seller (Etsy refused a copy, two settings match, ...).
    waiting: list[ShopProfileNeed] = Field(default_factory=list)
    #: Profiles started by this request.
    started: int = 0


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
        return [c.id for c in (await group_shops(session, tenant.id, shops)).get(group.id, [])]
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
    started = await profile_shops.start_links(session, profile, targets)
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
    rows = {r.connection_id: r for r in (await session.execute(
        select(ProfileShopLink).where(ProfileShopLink.profile_id == profile.id))).scalars()}
    shops: list[ShopSetup] = []
    for link in out.links:
        if link.main:
            continue
        left = plan.get(str(link.connection_id))
        row = rows.get(link.connection_id)
        settings = _settings_of(profile, row, left or {}) if row is not None and link.status != "checking" else []
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
            settings=settings, choices_expired=bool(link.missing) and left is None,
        ))
    return SetupOut(profile=out, shops=shops)


def _settings_of(profile: ListingProfile, link: ProfileShopLink, left: dict[str, Any]) -> list[SettingUse]:
    """Per setting: the shop's matching one (same name or same terms) when there is
    one; else a copy of the main shop's, preselected (created only after the seller
    confirms); else the seller chooses. Every choice can be changed."""
    values = L.link_values(link)
    notes = link.notes or {}
    out: list[SettingUse] = []
    for resource in L.RESOURCES:
        if L.wanted(profile.cached_payload, resource) in (None, "", []):
            continue
        entry = left.get(resource) or {}
        value = values[L.PAYLOAD_KEY[resource]]
        ids = [int(i) for i in (value if isinstance(value, list) else [value])] if value not in (None, []) else []
        options = [Choice(**o) for o in entry.get("options") or [] if o.get("id") is not None]
        if ids:
            by_id = {o.id: o.label for o in options}
            same = entry.get("linked") == value
            out.append(SettingUse(
                resource=resource, label=L.LABEL[resource], source_label=entry.get("source_label"), linked_ids=ids,
                linked_label=(entry.get("label") if same else None) or ", ".join(by_id.get(i, f"#{i}") for i in ids),
                how=entry.get("how") if same else "your choice", default="keep", options=options,
                creatable=bool(entry.get("creatable")) and entry.get("how") == "your choice",
                create_summary=_summary(resource, entry.get("create")), requests=int(entry.get("requests") or 0),
            ))
            continue
        creatable = bool(entry.get("creatable"))
        failed = entry.get("failure")
        failure = SetupFailure(**{k: failed.get(k) for k in ("reason", "action", "field")}) if failed else None
        out.append(SettingUse(
            resource=resource, label=L.LABEL[resource], source_label=entry.get("source_label"),
            # A copy Etsy just refused is not preselected again: the same request would be refused.
            default="copy" if creatable and failure is None else "choose",
            reason=(failure.reason if failure else None) or entry.get("reason") or notes.get(resource) or L.NOT_FOUND,
            creatable=creatable, create_summary=_summary(resource, entry.get("create")),
            requests=int(entry.get("requests") or 0), options=options, failure=failure,
        ))
    return out


def _pick(link: ProfileShopLink, shop_plan: dict[str, Any], resource: str, ids: list[int]) -> None:
    """The seller's choice of one of the shop's own settings (only ids Etsy listed for it)."""
    entry = shop_plan.get(resource)
    allowed = {int(o["id"]) for o in (entry or {}).get("options") or [] if o.get("id") is not None}
    if not entry or not ids or not set(ids) <= allowed:
        raise HTTPException(status_code=409, detail="choose from the shop's current list; check the shop again first")
    if resource != L.PARTNERS and len(ids) != 1:
        raise HTTPException(status_code=422, detail="choose one")
    value: Any = sorted(ids) if resource == L.PARTNERS else ids[0]
    setattr(link, L.PAYLOAD_KEY[resource], value)
    notes = dict(link.notes or {})
    notes.pop(resource, None)
    link.notes = notes or None
    link.status = "incomplete" if notes else "ready"
    names = {int(o["id"]): o.get("label") for o in entry.get("options") or [] if o.get("id") is not None}
    shop_plan[resource] = {
        "linked": value, "how": "your choice", "label": ", ".join(str(names.get(i)) for i in sorted(ids)),
        "options": entry.get("options") or [], "source_label": entry.get("source_label"),
        # A copy stays possible while the shop has no match of its own.
        "creatable": bool(entry.get("creatable")), "requests": entry.get("requests"), "create": entry.get("create"),
    }


async def _remember_pick(
    session: AsyncSession, profile: ListingProfile, shop_id: uuid.UUID, resource: str, ids: list[int],
    shop_plan: dict[str, Any],
) -> None:
    """The seller's choice of one of a shop's own settings is the account's for that
    setting and shop: every reference listing using the same setting uses it there too
    (``pipeline/setting_map.py``). Production partners stay the profile's own."""
    if resource not in setting_map.MAPPED or len(ids) != 1:
        return
    source_id = L.wanted(profile.cached_payload, resource)
    if source_id in (None, "", []):
        return
    label = (shop_plan.get(resource) or {}).get("label")
    await setting_map.remember(session, profile.tenant_id, resource, profile.connection_id, int(source_id), shop_id,
                               ids[0], how="your choice", label=label)


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
    _pick(link, shop_plan, body.resource, body.ids)
    plan[str(connection_id)] = shop_plan
    await _remember_pick(session, profile, connection_id, body.resource, body.ids, shop_plan)
    await setting_map.apply(session, tenant.id, target=connection_id)
    await session.commit()
    await _save_plan(redis, profile.id, plan)
    return await _out(session, tenant, profile)


@router.post("/{profile_id}/setup/apply", response_model=CreateOut)
async def apply_setup(
    profile_id: uuid.UUID,
    body: ApplyIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Any = Depends(get_redis),
) -> CreateOut:
    """The setup dialog's one confirm: every pick of a shop's own setting, and every
    copy of the main shop's setting to create (the only time anything is created).
    Every choice is checked before any is written: one wrong choice changes nothing."""
    profile = await _get(session, tenant, profile_id)
    plan = await _plan(redis, profile.id)
    copies: dict[uuid.UUID, set[str]] = {}
    requests = 0
    links: dict[uuid.UUID, ProfileShopLink] = {}
    for choice in body.choices:
        if await owned_shop(session, tenant.id, choice.connection_id) is None:
            raise HTTPException(status_code=404, detail="shop not found")
        if choice.connection_id not in links:
            links[choice.connection_id] = await _link(session, profile, choice.connection_id)
        entry = (plan.get(str(choice.connection_id)) or {}).get(choice.resource) or {}
        if choice.use == "copy":
            current = getattr(links[choice.connection_id], L.PAYLOAD_KEY[choice.resource])
            matched = current not in (None, []) and entry.get("how") != "your choice"
            if not entry.get("creatable") or choice.resource not in L.CREATABLE or matched:
                raise HTTPException(
                    status_code=409,
                    detail=f"{L.LABEL[choice.resource]} cannot be copied to that shop now; check the shop again first",
                )
            requests += int(entry.get("requests") or 0)
            copies.setdefault(choice.connection_id, set()).add(choice.resource)
        else:
            allowed = {int(o["id"]) for o in entry.get("options") or [] if o.get("id") is not None}
            if not choice.ids or not set(choice.ids) <= allowed:
                raise HTTPException(status_code=409, detail="choose from the shop's current list; check the shop again first")
    for choice in body.choices:
        if choice.use == "pick":
            shop_plan = plan.setdefault(str(choice.connection_id), {})
            _pick(links[choice.connection_id], shop_plan, choice.resource, choice.ids)
            await _remember_pick(session, profile, choice.connection_id, choice.resource, choice.ids, shop_plan)
    for shop_id, resources in copies.items():
        link = links[shop_id]
        for resource in resources:  # the copy replaces an earlier pick, for every reference listing
            setattr(link, L.PAYLOAD_KEY[resource], None)
            source_id = L.wanted(profile.cached_payload, resource)
            if resource in setting_map.MAPPED and source_id not in (None, "", []):
                row = await setting_map.lookup(session, tenant.id, resource, profile.connection_id, int(source_id), shop_id)
                if row is not None:
                    await setting_map.forget(session, row)
        link.pending_create = sorted(resources)
        link.status = "checking"
    for shop_id in {c.connection_id for c in body.choices if c.use == "pick"}:
        await setting_map.apply(session, tenant.id, target=shop_id)
    await session.commit()
    await _save_plan(redis, profile.id, plan)
    if copies:
        await enqueuer.enqueue("create_link_resources", str(profile.id))
    return CreateOut(profile=await _out(session, tenant, profile), requests=requests)


@router.post("/{profile_id}/setup/{connection_id}/check", response_model=schemas.ProfileOut)
async def check_shop_again(
    profile_id: uuid.UUID,
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Any = Depends(get_redis),
) -> schemas.ProfileOut:
    """Refresh: read the shop's settings from Etsy again (choices expired, or the seller made
    or changed one in Shop Manager), and resolve the profile there again."""
    profile = await _get(session, tenant, profile_id)
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    link = await _link(session, profile, connection_id)
    # Refresh is pressed in the setup dialog: setting the profile up there is what the seller asked.
    link.status, link.pending_create = "checking", [L.COPY_ALL]
    await forget_lists(redis, connection_id)
    await session.commit()
    await enqueuer.enqueue("link_profile", str(profile.id))
    return await _out(session, tenant, profile)


async def _others(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID) -> list[ListingProfile]:
    return list((await session.execute(select(ListingProfile).where(
        ListingProfile.tenant_id == tenant.id, ListingProfile.confirmed.is_(True),
        ListingProfile.connection_id != connection_id,
    ).order_by(ListingProfile.created_at))).scalars())


async def _shop_profiles(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID) -> ShopProfilesOut:
    profiles = await _others(session, tenant, connection_id)
    links = {link.profile_id: link for link in (await session.execute(select(ProfileShopLink).where(
        ProfileShopLink.connection_id == connection_id,
        ProfileShopLink.profile_id.in_([p.id for p in profiles]) if profiles else sa_false(),
    ))).scalars()}
    out = ShopProfilesOut(connection_id=connection_id, total=len(profiles))
    for profile in profiles:
        state = profile_shops.state_of(profile, links.get(profile.id))
        if state is None:
            out.not_set_up += 1
        elif state.status == "checking":
            out.checking += 1
        elif state.ready:
            out.set_up += 1
        else:
            out.waiting.append(ShopProfileNeed(id=profile.id, name=profile.name, reason=state.reason))
    return out


@router.get("/shop/{connection_id}", response_model=ShopProfilesOut)
async def shop_profiles_status(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> ShopProfilesOut:
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    return await _shop_profiles(session, tenant, connection_id)


@router.post("/shop/{connection_id}/use-all", response_model=ShopProfilesOut)
async def use_all_profiles_here(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ShopProfilesOut:
    """"Use all reference listings here": every confirmed profile of the account set up in this
    shop in one click. That click is the seller's confirmation: each setting the shop has no
    counterpart for is copied there (the account's map and same-name/same-terms settings
    first). Set-up or being-set-up profiles are left as they are. No dialog unless Etsy
    refuses something; the Shops page then opens it for those profiles."""
    if await owned_shop(session, tenant.id, connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    started: list[uuid.UUID] = []
    for profile in await _others(session, tenant, connection_id):
        if await profile_shops.start_links(session, profile, [connection_id]):
            started.append(profile.id)
    await session.commit()
    for profile_id in started:
        await enqueuer.enqueue("link_profile", str(profile_id))
    out = await _shop_profiles(session, tenant, connection_id)
    out.started = len(started)
    return out


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
