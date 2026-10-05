"""Publishing one piece of generated content to one or more shops (v5 §E, v8 §C).

A profile is the account's: the listing's own profile builds its draft in every
shop it is linked to, with that shop's own shipping profile, return policy,
processing profile and production partners (pipeline/links.py). The title, tags
and description are the listing's own everywhere. A shop the profile is not
linked to (or not completely) cannot be chosen until the seller sets the profile
up there, and the reason is shown with that one action. When the seller picks a
different profile for a shop, the title's prefix is swapped for that profile's
(trailing phrases dropped if that pushes it past 140 characters) and the
description is that profile's reference body under the new title.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile, ProfileShopLink
from app.pipeline import profile_shops
from app.pipeline.content import (
    bounds_for,
    MAX_TITLE_LENGTH,
    join_prefix,
    TITLE_TOO_SHORT,
    GeneratedListing,
    policy_for,
    validate_listing,
)
from app.pipeline.reference import PAYLOAD_VERSION, replace_title_block
from app.pipeline.reference import with_opening
from app.pipeline.search_rules import TitleRules

# Typical Etsy requests to create one draft: create, read back, category
# attributes, inventory and the images. Used for the estimate shown before a
# multi-shop publish ("3 shops x 5 listings = ~225 requests"). The worker's own
# check before each job uses the worst case (workers/gate.py JOB_COST).
ESTIMATED_CALLS_PER_DRAFT = 15

logger = logging.getLogger(__name__)


@dataclass
class Target:
    connection: EtsyConnection
    profile: ListingProfile | None
    reason: str | None = None  # why this shop cannot take this listing
    title: str | None = None
    description: str | None = None
    #: The only thing wrong is that the profile's Etsy data needs refreshing.
    stale: bool = False
    #: The profile's link to this shop (None for its main shop's drafts' purposes
    #: is never the case: the main shop has a link too).
    link: ProfileShopLink | None = None
    #: What is wrong is that the profile is not set up in this shop: one click fixes it.
    setup: bool = False

    @property
    def ok(self) -> bool:
        return self.profile is not None and self.reason is None


def is_current(profile: ListingProfile) -> bool:
    """Read by this version of the app (an older read lacks fields the draft needs)."""
    return int((profile.cached_payload or {}).get("payload_version") or 1) >= PAYLOAD_VERSION


def is_fresh(profile: ListingProfile) -> bool:
    if not profile.cached_payload or profile.updated_at is None:
        return False
    if not is_current(profile):
        return False
    updated = profile.updated_at
    if updated.tzinfo is None:  # SQLite hands back naive datetimes
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds() < ListingProfile.CACHE_MAX_AGE_SECONDS


@dataclass(frozen=True)
class FittedTitle:
    title: str
    used_all: bool  # every phrase of the original title is still in it
    dropped: tuple[str, ...] = ()


def _phrases(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def retarget_title(
    title: str,
    from_prefix: str | None,
    to_prefix: str | None,
    *,
    max_length: int = MAX_TITLE_LENGTH,
) -> FittedTitle:
    """Swap one shop's title prefix for another's, adjusting by phrase.

    Titles are comma-separated phrases, and the prefix is part of the first one
    ("Comfort Colors® Funny Nurse Shirt, ...", v6 §C). The source shop's prefix
    comes off and the target shop's goes on. If a longer prefix pushes the title
    past ``max_length``, trailing phrases are dropped until it fits. A shorter
    prefix leaves every phrase in place: the title is simply shorter.
    """
    source = (from_prefix or "").strip()
    stripped = title.strip()
    if source and stripped.casefold().startswith(source.casefold()):
        # With or without the comma earlier titles put after it.
        stripped = stripped[len(source):].lstrip(" ,")
    phrases = _phrases(stripped)
    target = (to_prefix or "").strip()

    def join(kept: list[str]) -> str:
        return join_prefix(target, ", ".join(kept))

    kept = list(phrases)
    while len(kept) > 1 and len(join(kept)) > max_length:
        kept.pop()
    return FittedTitle(join(kept), len(kept) == len(phrases), tuple(phrases[len(kept):]))


def _widest(a: TitleRules, b: TitleRules) -> TitleRules:
    return TitleRules(min(a.min_length, b.min_length), max(a.max_length, b.max_length))


async def shop_profiles(session: AsyncSession, connection_id: uuid.UUID) -> list[ListingProfile]:
    """The confirmed profiles usable in one shop: its own and those linked to it."""
    return await profile_shops.shop_profiles(session, connection_id)


async def resolve_target(
    session: AsyncSession,
    content: GeneratedContent,
    connection: EtsyConnection,
    *,
    profile_id: uuid.UUID | None = None,
) -> Target:
    """Which profile builds this content's draft in ``connection``'s shop, and its text.

    The content's own profile, or ``profile_id`` when the seller chose another.
    Either must be one of the account's confirmed profiles, set up in this shop.
    """
    source = (
        await session.get(ListingProfile, content.listing_profile_id)
        if content.listing_profile_id
        else None
    )
    if source is not None and source.tenant_id != connection.tenant_id:
        source = None
    if profile_id is not None:
        chosen = await session.get(ListingProfile, profile_id)
        if chosen is None or chosen.tenant_id != connection.tenant_id or not chosen.confirmed:
            return Target(connection, None, "that profile is not one of your confirmed profiles")
    elif source is not None and source.confirmed:
        chosen = source
    else:
        return Target(connection, None, "choose a profile for this listing")

    link = await profile_shops.link_for(session, chosen, connection.id)
    if link is None and profile_id is None:
        # Same-named profiles of two shops that were not merged (their shared
        # settings differed, v8 §C) still serve each other's shops, as before:
        # the exact name, never a guess.
        twin = next((p for p in await profile_shops.shop_profiles(session, connection.id)
                     if p.connection_id == connection.id and p.content_template == chosen.content_template
                     and p.name.strip().casefold() == chosen.name.strip().casefold()),
                    None)
        if twin is not None:
            chosen = twin
            link = await profile_shops.link_for(session, chosen, connection.id)
    if link is None:
        return Target(connection, chosen, f'profile "{chosen.name}" is not set up in this shop', setup=True)
    if chosen.reference_listing_id is None:
        return Target(connection, chosen, f'profile "{chosen.name}" needs a reference listing in its main shop',
                      link=link)

    if not is_fresh(chosen):
        return Target(
            connection,
            chosen,
            f'the Etsy data for profile "{chosen.name}" is more than a day old; '
            "refresh the profile, then try again"
            if not chosen.cached_payload or is_current(chosen)
            else f'profile "{chosen.name}" was read by an older version of the app; '
            "refresh the profile, then try again",
            stale=True,
            link=link,
        )

    state = profile_shops.state_of(chosen, link)
    if state is not None and not state.ready:
        return Target(connection, chosen, f'profile "{chosen.name}" in this shop: {state.reason}', link=link,
                      setup=state.status != "checking")

    if source is not None and chosen.id == source.id:
        title, description = content.title or "", content.description or ""
    else:
        fitted = retarget_title(
            content.title or "", source.title_prefix if source else None, chosen.title_prefix
        )
        title = fitted.title
        body = str((chosen.cached_payload or {}).get("description", ""))
        opening = str(((content.attributes or {}).get("search") or {}).get("opening") or "")
        # A listing written with a design-specific opening keeps it in every shop;
        # the body below it is that shop's own.
        description = with_opening(body, opening) if opening else replace_title_block(body, title)
        errors = validate_listing(
            GeneratedListing(title=title, tags=list(content.tags or []), description=description),
            policy_for(chosen.content_template),
            # The title was written to its own profile's bounds; this shop's
            # profile may use the other style, so accept either range.
            title_rules=_widest(bounds_for(source), bounds_for(chosen)),
        )
        if not fitted.used_all:
            # Phrases were dropped to fit 140, so the title is as long as this shop's
            # prefix allows. A slightly short title beats refusing the whole shop;
            # the minimum only refuses a title that is short with every phrase in it.
            errors = [e for e in errors if not e.startswith(TITLE_TOO_SHORT)]
        if fitted.dropped:
            logger.info(
                "title for shop %s shortened to fit its prefix; dropped %d trailing phrase(s)",
                connection.id,
                len(fitted.dropped),
            )
        if errors:
            return Target(connection, chosen, "with this profile's title prefix: " + "; ".join(errors), link=link)
    return Target(connection, chosen, None, title, description, link=link)
