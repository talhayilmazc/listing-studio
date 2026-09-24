"""Publishing one piece of generated content to one or more shops (v5 §E).

Each shop builds its draft from **its own** profile: category, price, variations,
size charts and section all come from that shop's reference listing. The title
and tags are written once. For another shop, the title's prefix is swapped for
that shop's, and the description is that shop's reference body under the new
title. A shop with no suitable profile cannot be chosen, and the reason is shown.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile
from app.pipeline.content import GeneratedListing, policy_for, validate_listing
from app.pipeline.reference import replace_title_block

# Typical Etsy requests to create one draft: create, read back, category
# attributes, inventory and the images. Used for the estimate shown before a
# multi-shop publish ("3 shops x 5 listings = ~225 requests"). The worker's own
# check before each job uses the worst case (workers/gate.py JOB_COST).
ESTIMATED_CALLS_PER_DRAFT = 15


@dataclass
class Target:
    connection: EtsyConnection
    profile: ListingProfile | None
    reason: str | None = None  # why this shop cannot take this listing
    title: str | None = None
    description: str | None = None

    @property
    def ok(self) -> bool:
        return self.profile is not None and self.reason is None


def is_fresh(profile: ListingProfile) -> bool:
    if not profile.cached_payload or profile.updated_at is None:
        return False
    updated = profile.updated_at
    if updated.tzinfo is None:  # SQLite hands back naive datetimes
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds() < ListingProfile.CACHE_MAX_AGE_SECONDS


def retarget_title(title: str, from_prefix: str | None, to_prefix: str | None) -> str:
    """Swap one shop's title prefix for another's ("Comfort Colors®, ..." -> "...")."""
    body = title.strip()
    source = (from_prefix or "").strip()
    if source and body.lower().startswith(source.lower()):
        body = body[len(source):].lstrip(" ,")
    target = (to_prefix or "").strip()
    if target and not body.lower().startswith(target.lower()):
        body = f"{target}, {body}"
    return body


async def shop_profiles(session: AsyncSession, connection_id: uuid.UUID) -> list[ListingProfile]:
    rows = await session.execute(
        select(ListingProfile)
        .where(ListingProfile.connection_id == connection_id, ListingProfile.confirmed.is_(True))
        .order_by(ListingProfile.name)
    )
    return list(rows.scalars())


async def resolve_target(
    session: AsyncSession,
    content: GeneratedContent,
    connection: EtsyConnection,
    *,
    profile_id: uuid.UUID | None = None,
) -> Target:
    """Which profile of ``connection``'s shop builds this content's draft, and its text.

    With ``profile_id`` the seller chose; it must be a confirmed profile of that
    shop. Otherwise, in order: the content's own profile if it belongs to that
    shop; a same-named profile there; the only profile there with the same
    template. Anything else needs the seller to choose.
    """
    source = (
        await session.get(ListingProfile, content.listing_profile_id)
        if content.listing_profile_id
        else None
    )
    candidates = await shop_profiles(session, connection.id)
    chosen: ListingProfile | None = None
    if profile_id is not None:
        chosen = next((p for p in candidates if p.id == profile_id), None)
        if chosen is None:
            return Target(connection, None, "that profile is not a confirmed profile of this shop")
    elif source is not None and source.connection_id == connection.id:
        chosen = source
    else:
        template = source.content_template if source is not None else None
        same_kind = [p for p in candidates if template is None or p.content_template == template]
        named = [p for p in same_kind if source is not None and p.name.casefold() == source.name.casefold()]
        if named:
            chosen = named[0]
        elif len(same_kind) == 1:
            chosen = same_kind[0]
        elif not same_kind:
            kind = f"{template} " if template else ""
            return Target(connection, None, f"this shop has no confirmed {kind}profile")
        else:
            return Target(connection, None, "several profiles in this shop fit; choose one")

    if not is_fresh(chosen):
        return Target(
            connection,
            chosen,
            f'the Etsy data for profile "{chosen.name}" is more than a day old; '
            "refresh the profile, then try again",
        )

    if source is not None and chosen.id == source.id:
        title, description = content.title or "", content.description or ""
    else:
        title = retarget_title(
            content.title or "", source.title_prefix if source else None, chosen.title_prefix
        )
        body = str((chosen.cached_payload or {}).get("description", ""))
        description = replace_title_block(body, title)
        errors = validate_listing(
            GeneratedListing(title=title, tags=list(content.tags or []), description=description),
            policy_for(chosen.content_template),
        )
        if errors:
            return Target(connection, chosen, "with this shop's title prefix: " + "; ".join(errors))
    return Target(connection, chosen, None, title, description)
