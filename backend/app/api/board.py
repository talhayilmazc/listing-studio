"""The grouping board: the seller arranges a batch's photos into listing groups.

Photos move between groups and the Unsorted tray, a selection becomes a new
group, two groups merge, a group's SKU is edited. Everything is per account: a
batch or photo of another account is 404.

A group's written listing (title, tags, description) is about the design and
stays with the group: moving photos changes only its images. When its cover
changes, ``generated_content.asset_id`` follows the new cover and differs from
``written_from_asset_id``, so the page says the text came from the old cover and
offers "Regenerate". A group with a listing keeps at least one photo here (the
listing would lose its images); deleting photos is the way to remove it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.batches import _files_needed, _get_batch, get_batch
from app.api.deps import active_tenant, get_session
from app.db.models import Asset, AssetStatus, GeneratedContent, ListingGroupSetting, Tenant
from app.pipeline import grouping

router = APIRouter(prefix="/api", tags=["board"])

#: Longest group name / SKU the board accepts (Etsy's own SKU rules come with editing the SKU).
MAX_KEY = 120


def _norm(key: str | None) -> str:
    """Group keys as compared here: the root group is "" whether stored as "" or NULL."""
    return key or ""


def label(key: str) -> str:
    return "Unsorted" if grouping.is_unsorted(key) else (key or "Root folder")


async def _batch_assets(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> list[Asset]:
    rows = await session.execute(select(Asset).where(Asset.batch_id == batch_id, Asset.tenant_id == tenant.id))
    return list(rows.scalars())


def _groups(assets: Iterable[Asset]) -> dict[str, list[Asset]]:
    out: dict[str, list[Asset]] = {}
    for a in assets:
        out.setdefault(_norm(a.group_key), []).append(a)
    for members in out.values():
        members.sort(key=lambda a: (a.rank if a.rank is not None else 10_000, a.original_filename))
    return out


def _usable(a: Asset) -> bool:
    return a.status is AssetStatus.processed and a.processed_key is not None


def _rank(members: list[Asset]) -> None:
    """Number a group 1..n; an image that failed to process never leads."""
    usable = [a for a in members if _usable(a)]
    if usable and members[0] is not usable[0]:
        members.remove(usable[0])
        members.insert(0, usable[0])
    for rank, a in enumerate(members, start=1):
        a.rank = rank


async def _contents(session: AsyncSession, assets: list[Asset]) -> dict[uuid.UUID, list[GeneratedContent]]:
    """Written listings by the photo they sit on (the group's cover)."""
    if not assets:
        return {}
    rows = await session.execute(select(GeneratedContent).where(GeneratedContent.asset_id.in_([a.id for a in assets])))
    out: dict[uuid.UUID, list[GeneratedContent]] = {}
    for c in rows.scalars():
        out.setdefault(c.asset_id, []).append(c)
    return out


async def _setting(session: AsyncSession, batch_id: uuid.UUID, key: str) -> ListingGroupSetting | None:
    return (
        await session.execute(
            select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id, ListingGroupSetting.group_key == key)
        )
    ).scalar_one_or_none()


def _clean_key(text: str) -> str:
    key = " ".join(text.split())
    if not key:
        raise HTTPException(status_code=422, detail="Give the group a SKU or name.")
    if len(key) > MAX_KEY:
        raise HTTPException(status_code=422, detail=f"A group name can be at most {MAX_KEY} characters.")
    if grouping.is_unsorted(key) or key.startswith("~"):
        raise HTTPException(status_code=422, detail="A group name cannot start with “~”.")
    return key.upper() if grouping.sku_of(key) == key.upper() else key


def _sku_of_group(members: list[Asset], key: str) -> str | None:
    if grouping.is_unsorted(key):
        return None
    return next((a.parsed_sku for a in members if a.parsed_sku), None)


async def _apply(
    session: AsyncSession,
    tenant: Tenant,
    batch_id: uuid.UUID,
    groups: dict[str, list[Asset]],
    moving: list[Asset],
    target: str,
    *,
    sku: str | None,
    allow_emptying: str | None = None,
) -> None:
    """Move ``moving`` (in this order) to the end of ``target``, keeping every
    written listing on a photo of its own group."""
    _files_needed(*moving)
    moving_ids = {a.id for a in moving}
    contents = await _contents(session, [a for members in groups.values() for a in members])
    sources = {_norm(a.group_key) for a in moving} - {target}
    for key in sources:
        remaining = [a for a in groups[key] if a.id not in moving_ids]
        written = [c for a in groups[key] for c in contents.get(a.id, [])]
        if written and not remaining and key != allow_emptying:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{label(key)} has a listing written for it, so at least one photo has to stay in it. "
                    "Merge it into another group instead, or delete its photos to remove it."
                ),
            )
    destination = [a for a in groups.get(target, []) if a.id not in moving_ids]
    target_written = [c for a in groups.get(target, []) for c in contents.get(a.id, [])]
    carried: list[GeneratedContent] = []
    for key in sources:
        remaining = [a for a in groups[key] if a.id not in moving_ids]
        written = [c for a in groups[key] for c in contents.get(a.id, [])]
        if remaining:
            _rank(remaining)
            for c in written:  # the listing follows its group's cover
                c.asset_id = remaining[0].id
            continue
        # Emptied (only a merge may empty a group with a listing): its listing joins
        # the target, which must not have one; its settings carry over when the target has none.
        if written and target_written:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Both {label(key)} and {label(target)} have a listing written. Merging would leave one "
                    "listing without photos: move the photos you want instead."
                ),
            )
        carried += written
        setting = await _setting(session, batch_id, key)
        if setting is not None:
            if not grouping.is_unsorted(target) and await _setting(session, batch_id, target) is None:
                setting.group_key = target
            else:
                await session.delete(setting)
    for a in moving:
        a.group_key = target
        a.parsed_sku = sku
    members = destination + moving
    _rank(members)
    for c in target_written + carried:
        c.asset_id = members[0].id
    await session.flush()


async def _moving(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, ids: list[uuid.UUID]) -> tuple[dict[str, list[Asset]], list[Asset]]:
    assets = await _batch_assets(session, tenant, batch_id)
    by_id = {a.id: a for a in assets}
    missing = [i for i in dict.fromkeys(ids) if i not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="image not found")
    groups = _groups(assets)
    # In the order the seller sees them: group by group, then rank.
    order = {a.id: n for n, a in enumerate(a for key in sorted(groups) for a in groups[key])}
    moving = sorted((by_id[i] for i in dict.fromkeys(ids)), key=lambda a: order[a.id])
    return groups, moving


@router.post("/batches/{batch_id}/board/move", response_model=schemas.BatchDetail)
async def move_photos(
    batch_id: uuid.UUID,
    body: schemas.BoardMove,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchDetail:
    """Move photos to another group, to Unsorted, or into a new group."""
    await _get_batch(session, tenant, batch_id)
    groups, moving = await _moving(session, tenant, batch_id, body.asset_ids)
    if body.new_group:
        target = _clean_key(body.to)
        if target in groups:
            raise HTTPException(status_code=409, detail=f"There is already a group called {target}: move the photos into it instead.")
        sku = target if grouping.sku_of(target) == target else None
        # The new group starts with the shop and profile of the group the first photo came from.
        first = await _setting(session, batch_id, _norm(moving[0].group_key))
        if first is not None:
            session.add(
                ListingGroupSetting(
                    tenant_id=tenant.id, batch_id=batch_id, group_key=target, connection_id=first.connection_id,
                    profile_id=first.profile_id, size_chart_profile_id=first.size_chart_profile_id,
                )
            )
    else:
        target = _norm(body.to)
        if target not in groups and not grouping.is_unsorted(target):
            raise HTTPException(status_code=404, detail="group not found")
        sku = _sku_of_group(groups.get(target, []), target)
    await _apply(session, tenant, batch_id, groups, moving, target, sku=sku)
    await session.commit()
    return await get_batch(batch_id, session, tenant)


@router.post("/batches/{batch_id}/board/merge", response_model=schemas.BatchDetail)
async def merge_groups(
    batch_id: uuid.UUID,
    body: schemas.BoardMerge,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchDetail:
    """Put one group's photos into another. A listing written for the merged group
    moves with them when the other has none; both having one is refused."""
    await _get_batch(session, tenant, batch_id)
    source, target = _norm(body.from_key), _norm(body.into_key)
    if source == target:
        raise HTTPException(status_code=422, detail="Choose two different groups.")
    if grouping.is_unsorted(target):
        raise HTTPException(status_code=422, detail="Unsorted is not a group: move the photos there instead.")
    groups = _groups(await _batch_assets(session, tenant, batch_id))
    if source not in groups or target not in groups:
        raise HTTPException(status_code=404, detail="group not found")
    await _apply(
        session, tenant, batch_id, groups, list(groups[source]), target,
        sku=_sku_of_group(groups[target], target), allow_emptying=source,
    )
    await session.commit()
    return await get_batch(batch_id, session, tenant)


@router.put("/batches/{batch_id}/board/sku", response_model=schemas.BatchDetail)
async def set_group_sku(
    batch_id: uuid.UUID,
    body: schemas.BoardSku,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchDetail:
    """Set a group's SKU (Etsy's SKU rules, audited; api/skus.py). A group named
    after its SKU takes the new one as its name."""
    from app.api.skus import apply_group_sku

    await _get_batch(session, tenant, batch_id)
    await apply_group_sku(session, tenant, batch_id, _norm(body.group_key), body.sku)
    await session.commit()
    return await get_batch(batch_id, session, tenant)

