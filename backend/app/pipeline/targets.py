"""Publishing one piece of generated content to one or more shops (v5 §E).

Each shop builds its draft from **its own** profile: category, price, variations,
size charts and section all come from that shop's reference listing. The title
and tags are written once. For another shop, the title's prefix is swapped for
that shop's (trailing phrases dropped if that pushes it past 140 characters),
and the description is that shop's reference body under the new title. A shop with no suitable profile cannot be chosen, and the reason is shown.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile
from app.pipeline.content import (
    MAX_TITLE_LENGTH,
    join_prefix,
    TITLE_TOO_SHORT,
    GeneratedListing,
    policy_for,
    validate_listing,
)
from app.pipeline.reference import replace_title_block

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
        fitted = retarget_title(
            content.title or "", source.title_prefix if source else None, chosen.title_prefix
        )
        title = fitted.title
        body = str((chosen.cached_payload or {}).get("description", ""))
        description = replace_title_block(body, title)
        errors = validate_listing(
            GeneratedListing(title=title, tags=list(content.tags or []), description=description),
            policy_for(chosen.content_template),
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
            return Target(connection, chosen, "with this shop's title prefix: " + "; ".join(errors))
    return Target(connection, chosen, None, title, description)
