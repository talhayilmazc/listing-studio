"""A listing in one shop: its own profile and SKU there, and where it stands there.

A written listing (one design, one ``generated_content``) can be on Etsy in several shops,
one draft each (``listing_publication``). Its title, tags, description and photos are the
same everywhere. Its **profile** and **SKU** can differ per shop
(``generated_content.shop_overrides`` = ``{connection id: {"profile_id", "sku"}}``): a
seller who published a batch in one shop sends the same listings to another shop with that
shop's own profile and SKUs ("Publish in another shop", api/another_shop.py), or edits the
SKU or profile while that shop is shown on the batch page. A shop or field not in the entry
follows the listing: its own profile and its group's SKU.

Nothing here changes a listing already on Etsy in a shop: what is there is edited on Etsy
(or with "Update SKU on Etsy", which names the shop it changes).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GeneratedContent, ListingPublication, PlannedSlot

#: Where a listing stands in one shop, in the order the batch page shows them.
NOT_SENT = "not_sent"
DRAFTED = "drafted"
SCHEDULED = "scheduled"
PUBLISHED = "published"
STATES = (NOT_SENT, DRAFTED, SCHEDULED, PUBLISHED)
#: Planned slots still to come (a group schedule's draft or go-live).
OPEN_SLOTS = ("waiting", "drafting", "scheduled")

_KEEP: Any = object()


def entry(content: GeneratedContent, shop_id: uuid.UUID | str) -> dict[str, Any]:
    raw = (content.shop_overrides or {}).get(str(shop_id))
    return dict(raw) if isinstance(raw, dict) else {}


def profile_in(content: GeneratedContent, shop_id: uuid.UUID | str) -> uuid.UUID | None:
    """The profile chosen for this listing in this shop only, or None (its own profile)."""
    value = entry(content, shop_id).get("profile_id")
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def sku_override(content: GeneratedContent, shop_id: uuid.UUID | str) -> str | None:
    value = entry(content, shop_id).get("sku")
    return str(value).strip() or None if value else None


def sku_in(content: GeneratedContent, base: str | None, shop_id: uuid.UUID | str) -> str | None:
    """The SKU this listing carries in this shop: its own there, else the group's (``base``)."""
    return sku_override(content, shop_id) or (base or None)


def set_override(
    content: GeneratedContent, shop_id: uuid.UUID | str, *, profile_id: Any = _KEEP, sku: Any = _KEEP
) -> None:
    """Set (or, with None, clear) this listing's profile or SKU in one shop. An entry left
    empty is dropped; the column goes back to NULL when no shop has one."""
    stored = {k: dict(v) for k, v in (content.shop_overrides or {}).items() if isinstance(v, dict)}
    mine = stored.get(str(shop_id), {})
    if profile_id is not _KEEP:
        if profile_id is None:
            mine.pop("profile_id", None)
        else:
            mine["profile_id"] = str(profile_id)
    if sku is not _KEEP:
        if not sku:
            mine.pop("sku", None)
        else:
            mine["sku"] = str(sku)
    if mine:
        stored[str(shop_id)] = mine
    else:
        stored.pop(str(shop_id), None)
    content.shop_overrides = stored or None  # a new value: JSON changes are seen


def replace_prefix(sku: str | None, old: str, new: str) -> tuple[str, bool]:
    """``sku`` with its leading ``old`` replaced by ``new`` (any case), and whether it started
    with ``old``. An empty ``old`` puts ``new`` in front of every SKU. One that does not
    start with ``old`` is kept as it is. The TS copy is lib/anotherShop.ts::replacePrefix."""
    value = (sku or "").strip()
    old, new = (old or "").strip(), (new or "").strip()
    if not old:
        return new + value, True
    if value.casefold().startswith(old.casefold()):
        return new + value[len(old):], True
    return value, False


async def states(
    session: AsyncSession, contents: Iterable[GeneratedContent]
) -> dict[uuid.UUID, dict[uuid.UUID, str]]:
    """Per listing, per shop it has anything in: published, scheduled or drafted (a shop not
    listed is not sent). A draft deleted on Etsy is not counted; a group schedule's slot to
    come counts as scheduled."""
    ids = [c.id for c in contents]
    out: dict[uuid.UUID, dict[uuid.UUID, str]] = {cid: {} for cid in ids}
    if not ids:
        return out
    for pub in (await session.execute(select(ListingPublication).where(
            ListingPublication.content_id.in_(ids), ListingPublication.state != "deleted_on_etsy"))).scalars():
        if pub.content_id is None:
            continue
        if pub.state == "active":
            state = PUBLISHED
        elif pub.scheduled_for is not None and pub.schedule_note is None:
            state = SCHEDULED
        else:
            state = DRAFTED
        out[pub.content_id][pub.connection_id] = state
    for slot in (await session.execute(select(PlannedSlot).where(
            PlannedSlot.content_id.in_(ids), PlannedSlot.state.in_(OPEN_SLOTS)))).scalars():
        if out[slot.content_id].get(slot.connection_id) != PUBLISHED:
            out[slot.content_id][slot.connection_id] = SCHEDULED
    return out


def sent(cells: dict[uuid.UUID, str], shop_id: uuid.UUID | None = None) -> bool:
    """Whether the listing was sent to this shop (a draft, a schedule or live), or to any:
    what is there was built with the profile and SKU it had, and is edited on Etsy."""
    if shop_id is None:
        return any(s != NOT_SENT for s in cells.values())
    return cells.get(shop_id, NOT_SENT) != NOT_SENT
