"""A listing in one shop: its own profile, SKU and text there, and where it stands there.

A written listing (one design, one ``generated_content``) can be on Etsy in several shops,
one draft each (``listing_publication``). Its title, tags, description and photos are the
same everywhere unless the seller gives a shop its own. Its **profile**, **SKU** and
**title, tags and description** can differ per shop (``generated_content.shop_overrides`` =
``{connection id: {"profile_id", "sku", "title", "tags", "description"}}``; no migration
beyond 0093, the same column): a seller who published a batch in one shop sends the same
listings to another shop with that shop's own profile, SKUs and edited text ("Publish in
another shop", api/another_shop.py), or edits the SKU, profile or the review card's text
while that shop is shown. A shop or field not in the entry follows the listing: its own
profile, its group's SKU and its own text. Each text field stands alone (a shop with its
own title keeps the listing's tags).

Nothing here changes a listing already on Etsy in a shop: what is there is edited on Etsy
(or with "Update SKU on Etsy", which names the shop it changes).

**One rule for a listing's text in a shop** (production bug, 2026-10-27): :func:`text_for` is
the only answer to "what title, tags and description does this listing have in that shop": the
shop's own field if it has one, else the listing's own. Every reader uses it: the workspace
rows, its send summary and the send, the draft (pipeline/targets.py), the review card, the
go-live check, "Generate the empty ones" and ``workspace-check``.

**One rule for approval in a shop** (:func:`approved_in`): a listing approved once is approved
for every shop, unless its text for that shop was edited after it was approved; then that shop
says "Edited for <shop>, approve again" until the seller approves it there (``approve_in``) or
approves the listing again.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from datetime import datetime, timezone

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
#: The text fields a shop can have its own of.
TEXT_FIELDS = ("title", "tags", "description")
#: When this shop's text was last changed, and when the listing was approved for this shop.
TEXT_EDITED_AT = "text_edited_at"
SHOP_APPROVED_AT = "approved_at"


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


def text_in(content: GeneratedContent, shop_id: uuid.UUID | str) -> dict[str, Any]:
    """The text fields this listing has of its own in this shop (none: it follows the listing)."""
    mine = entry(content, shop_id)
    out: dict[str, Any] = {}
    for name in TEXT_FIELDS:
        if name in mine and mine[name] is not None:
            out[name] = list(mine[name]) if name == "tags" else str(mine[name])
    return out


def text_for(content: GeneratedContent, shop_id: uuid.UUID | str | None) -> tuple[str, list[str], str]:
    """(title, tags, description) as this listing goes to this shop."""
    own = text_in(content, shop_id) if shop_id is not None else {}
    return (own.get("title", content.title or ""), list(own.get("tags", content.tags or [])),
            own.get("description", content.description or ""))


def set_override(
    content: GeneratedContent, shop_id: uuid.UUID | str, *, profile_id: Any = _KEEP, sku: Any = _KEEP,
    title: Any = _KEEP, tags: Any = _KEEP, description: Any = _KEEP,
) -> None:
    """Set (or, with None, clear) this listing's profile, SKU or a text field in one shop. An
    entry left empty is dropped; the column goes back to NULL when no shop has one."""
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
    before_text = {k: mine.get(k) for k in TEXT_FIELDS}
    for name, value in (("title", title), ("tags", tags), ("description", description)):
        if value is _KEEP:
            continue
        if value is None:
            mine.pop(name, None)
        else:
            mine[name] = [str(t) for t in value] if name == "tags" else str(value)
    if not any(k in mine for k in TEXT_FIELDS):
        mine.pop(TEXT_EDITED_AT, None)  # back to the listing's text: its approval holds
        mine.pop(SHOP_APPROVED_AT, None)
    elif {k: mine.get(k) for k in TEXT_FIELDS} != before_text:
        mine[TEXT_EDITED_AT] = datetime.now(timezone.utc).isoformat()
    if mine:
        stored[str(shop_id)] = mine
    else:
        stored.pop(str(shop_id), None)
    content.shop_overrides = stored or None  # a new value: JSON changes are seen


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def edited_after_approval(content: GeneratedContent, shop_id: uuid.UUID | str) -> bool:
    """The listing is approved, but its text for this shop changed after that (and was not
    approved there since): that shop needs it approved again."""
    if not content.approved:
        return False
    mine = entry(content, shop_id)
    edited = _when(mine.get(TEXT_EDITED_AT))
    if edited is None:
        return False
    approved = [a for a in (_when(content.approved_at.isoformat() if content.approved_at else None),
                            _when(mine.get(SHOP_APPROVED_AT))) if a is not None]
    return not approved or max(approved) < edited


def approved_in(content: GeneratedContent, shop_id: uuid.UUID | str) -> bool:
    """Approved for this shop: approved once, and its text there not edited since."""
    return bool(content.approved) and not edited_after_approval(content, shop_id)


def approve_in(content: GeneratedContent, shop_id: uuid.UUID | str) -> None:
    """The seller approved this listing for this shop ("Approve selected" in the workspace).
    Only kept when the shop has text of its own (otherwise the listing's approval answers)."""
    stored = {k: dict(v) for k, v in (content.shop_overrides or {}).items() if isinstance(v, dict)}
    mine = stored.get(str(shop_id))
    if not mine or not any(k in mine for k in TEXT_FIELDS):
        return
    mine[SHOP_APPROVED_AT] = datetime.now(timezone.utc).isoformat()
    content.shop_overrides = stored


def text_source(content: GeneratedContent, shop_id: uuid.UUID | str | None) -> dict[str, str]:
    """Where each text field comes from in this shop: "shop" (its own) or "listing"."""
    own = text_in(content, shop_id) if shop_id is not None else {}
    return {name: "shop" if name in own else "listing" for name in TEXT_FIELDS}


#: A production partner in a shop: needed (Etsy refuses a listing made by someone else without
#: one), a quiet note (a print-on-demand looking profile names none), or fine.
PARTNER_REQUIRED = "required"
PARTNER_NOTE = "note"
PARTNER_OK = "ok"


def partner_state(profile: Any, link: Any = None) -> str:
    """Whether a draft built with ``profile`` in a shop needs a production partner.

    Etsy's rule as the draft path applies it (etsy/publisher.py: ``production_partner_ids``
    is required only when ``who_made`` is "someone_else"): such a profile with no partner in
    that shop (its main shop: the reference's; another shop: the ones linked there) is refused,
    so it is stopped before anything is sent. A print-on-demand looking profile that names no
    partner, the seller having said they make it, is only a note (pipeline/creativity.py).
    """
    from app.pipeline import creativity

    if profile is None:
        return PARTNER_OK
    payload = getattr(profile, "cached_payload", None) or {}
    partners = (link.production_partner_ids if link is not None and getattr(link, "connection_id", None) != profile.connection_id
                else payload.get("production_partner_ids"))
    if payload.get("who_made") == "someone_else" and not partners:
        return PARTNER_REQUIRED
    return PARTNER_NOTE if creativity.production_note(profile) else PARTNER_OK


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
