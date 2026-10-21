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
from app.compliance.trademarks import tenant_blocklist
from app.pipeline import manual, profile_shops, title_fit
from app.pipeline.content import (
    bounds_for,
    MAX_TITLE_LENGTH,
    join_prefix,
    TITLE_TOO_LONG,
    TITLE_TOO_SHORT,
    GeneratedListing,
    check_listing,
    policy_for,
)
from app.pipeline.reference import PAYLOAD_VERSION, replace_title_block
from app.pipeline.reference import with_opening
from app.pipeline.search_rules import TitleRules

# Requests per draft until enough drafts are measured. Plans, the publish
# preview and the gate use the measured average + margin instead
# (core/request_cost.py); this is its default.
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
    #: What is wrong is the listing's own text under this shop's profile (its
    #: title or tags), not the shop's setup.
    by_text: bool = False
    #: The title is over 140 characters with this shop's prefix: the draft job rewrites it
    #: once for this shop (pipeline/title_fit.py), else stops and flags the card. Never cut.
    title_fit: bool = False

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
    used_all: bool  # every phrase of the original title is still in it (always, now)
    dropped: tuple[str, ...] = ()
    #: Over ``max_length`` with the new prefix: kept whole, for the one rewrite (title_fit).
    too_long: bool = False


def _phrases(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def retarget_title(
    title: str,
    from_prefix: str | None,
    to_prefix: str | None,
    *,
    max_length: int = MAX_TITLE_LENGTH,
) -> FittedTitle:
    """Swap one shop's title prefix for another's.

    Titles are comma-separated phrases, and the prefix is part of the first one
    ("Comfort Colors® Funny Nurse Shirt, ...", v6 §C). The source shop's prefix
    comes off and the target shop's goes on. Nothing is ever dropped: a longer
    prefix that pushes the title past ``max_length`` keeps it whole and marks it
    ``too_long`` (the draft job rewrites it once for that shop, else flags the card;
    trailing phrases used to be dropped silently, only logged).
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

    whole = join(phrases)
    return FittedTitle(whole, True, (), too_long=len(whole) > max_length)


def _widest(a: TitleRules, b: TitleRules) -> TitleRules:
    return TitleRules(min(a.min_length, b.min_length), max(a.max_length, b.max_length))


async def shop_profiles(session: AsyncSession, connection_id: uuid.UUID) -> list[ListingProfile]:
    """The confirmed profiles usable in one shop: its own and those linked to it."""
    return await profile_shops.shop_profiles(session, connection_id)


def retext(
    content: GeneratedContent, source: ListingProfile | None, chosen: ListingProfile
) -> tuple[str, str, FittedTitle]:
    """The listing's title and description as ``chosen`` builds them, when it was
    written with ``source``: the title's prefix is swapped for the new profile's
    (never cut: one too long for 140 characters is marked for a rewrite) and the
    description is the new profile's reference body under the listing's opening
    (short style) or title (long style). A title the seller has not written yet
    stays empty."""
    from app.pipeline import manual

    fitted = retarget_title(content.title or "", source.title_prefix if source else None, chosen.title_prefix)
    if manual.is_manual(content):
        # The seller's own title: no prefix was added for them, so none is swapped.
        fitted = FittedTitle(content.title or "", True)
        return fitted.title, manual.description_for(chosen, fitted.title), fitted
    title = fitted.title
    body = str((chosen.cached_payload or {}).get("description", ""))
    opening = str(((content.attributes or {}).get("search") or {}).get("opening") or "")
    # A listing written with a design-specific opening keeps it in every shop;
    # the body below it is that profile's own.
    description = with_opening(body, opening) if opening else replace_title_block(body, title)
    return title, description, fitted


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
        title, description, fitted = retext(content, source, chosen)
        stored = title_fit.stored_for(content, connection.id)
        if fitted.too_long and stored:
            # Rewritten once for this shop already, from this very title.
            description = description.replace(title, stored, 1) if title and description.startswith(title) else description
            title, fitted = stored, FittedTitle(stored, True)
        errors = check_listing(
            GeneratedListing(title=title, tags=list(content.tags or []), description=description),
            policy_for(chosen.content_template),
            # A listing the seller wrote: only what Etsy refuses (and the trademark filter).
            hand_written=manual.is_manual(content),
            # The account's own trademark decision, the same in every shop (v8 §E):
            # never the app's default, which would block in one shop what another allows.
            trademarks=await tenant_blocklist(session, content.tenant_id),
            for_seller=True,
            # The title was written to its own profile's bounds; this shop's
            # profile may use the other style, so accept either range.
            title_rules=_widest(bounds_for(source), bounds_for(chosen)),
        ).errors
        if fitted.too_long:
            # Over 140 only because of this shop's prefix: the draft job rewrites it once
            # for this shop (title_fit), or stops before Etsy and flags the card.
            errors = [e for e in errors if not e.startswith((TITLE_TOO_LONG, "title is over Etsy's"))]
        if stored and title == stored:
            # The rewrite for this shop may sit a little under the minimum (title_fit.problems).
            errors = [e for e in errors if not e.startswith(TITLE_TOO_SHORT)]
        if errors:
            return Target(connection, chosen, "with this profile's title prefix: " + "; ".join(errors), link=link, by_text=True)
        return Target(connection, chosen, None, title, description, link=link, title_fit=fitted.too_long)
    return Target(connection, chosen, None, title, description, link=link)
