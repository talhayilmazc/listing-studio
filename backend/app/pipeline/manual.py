"""A listing whose title and tags the seller writes themselves: no AI.

No vision call and no content call is made, so nothing is metered and no model
is recorded. Everything else is the group's profile, exactly as for a listing
the app wrote: the description is the profile's reference description, the
category, price, variations, shipping, return policy, size charts and SKU come
from the profile when the draft is made, and Occasion / Holiday start from the
profile's defaults.

What a manual listing does not have, because it comes from looking at the
design: the theme (used to suggest a shop section), colours and other optional
attributes read from the artwork, and the check for recognisable characters in
the drawing. The trademark filter still reads its title, tags and description.

The title starts empty and so do the 13 tags. In the "long" style a description
opens with the listing's title (the reference's own title block is replaced by
it), so that block follows the title the seller types for as long as they have
not edited the description themselves (:func:`follow_title`).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.scanner import rescan
from app.db.models import Asset, GeneratedContent, ListingProfile
from app.pipeline import item_options
from app.pipeline.content import uses_search_style
from app.pipeline.reference import replace_title_block, with_opening

AI, MANUAL = "ai", "manual"
SOURCES = (AI, MANUAL)


def is_manual(content: Any) -> bool:
    return getattr(content, "source", AI) == MANUAL


def description_for(profile: ListingProfile | None, title: str) -> str:
    """The profile's reference description for a listing titled ``title``.

    Long style: the reference's title block becomes ``title``. Short style (and a
    listing with no title yet): the body alone, without the reference's own
    title block, which described the reference listing.
    """
    body = str(((profile.cached_payload or {}) if profile is not None else {}).get("description", ""))
    title = (title or "").strip()
    if not body.strip():
        return title  # no reference description to carry over
    if title and not (profile is not None and uses_search_style(profile)):
        return replace_title_block(body, title)
    return with_opening(body, "").lstrip("\n")


def follow_title(content: GeneratedContent, profile: ListingProfile | None, old_title: str | None) -> bool:
    """Keep the description's title block in step with the title.

    Only while the description is still what the app built (from the title
    before this change, or from no title at all, which is what a card that has
    not reloaded sends back): once the seller edited it, it is theirs and is
    left alone.
    """
    if not is_manual(content):
        return False
    built = {description_for(profile, old_title or ""), description_for(profile, "")}
    if (content.description or "") not in built:
        return False
    updated = description_for(profile, content.title or "")
    changed = updated != (content.description or "")
    content.description = updated
    return changed


def is_empty(content: Any, profile: ListingProfile | None) -> bool:
    """A listing the seller writes that nothing was typed into yet: no title, no
    tags, and the description still the profile's (or none). "Generate the empty
    ones" writes only these, never one the seller started."""
    if not is_manual(content) or (content.title or "").strip() or list(content.tags or []):
        return False
    if any(isinstance(v, dict) and any(v.get(k) for k in ("title", "tags", "description"))
           for v in (content.shop_overrides or {}).values()):
        return False  # text typed for a shop is the seller's: never written over
    return (content.description or "") in {"", description_for(profile, "")}


def default_attributes(profile: ListingProfile) -> dict[str, Any]:
    """The profile's default Occasion / Holiday, each only if Etsy lists it for the category."""
    payload = profile.cached_payload or {}
    chosen: dict[str, Any] = {}
    for name, default in ((item_options.OCCASION, profile.default_occasion), (item_options.HOLIDAY, profile.default_holiday)):
        options = item_options.options_for(payload, name)
        key = options.name if options is not None else name
        chosen = item_options.apply_profile_default(chosen, key, default, "", options)
    return chosen


async def write(
    session: AsyncSession, *, tenant_id: uuid.UUID, batch_id: uuid.UUID, asset: Asset, profile: ListingProfile
) -> GeneratedContent:
    """The empty listing the seller fills in, with its profile's description."""
    chosen = default_attributes(profile)
    content = GeneratedContent(
        tenant_id=tenant_id,
        batch_id=batch_id,
        asset_id=asset.id,
        # Nothing was read from the cover, so there is no cover it was written from.
        written_from_asset_id=None,
        title="",
        tags=[],
        description=description_for(profile, ""),
        taxonomy_id=(profile.cached_payload or {}).get("taxonomy_id"),
        listing_profile_id=profile.id,
        attributes={"listing": chosen} if chosen else None,
        source=MANUAL,
        approved=False,
    )
    session.add(content)
    await session.flush()
    await rescan(session, content)  # the trademark filter reads the description it starts with
    asset.error = None
    return content
