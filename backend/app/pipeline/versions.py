"""Each version of a listing's text as it went to Etsy, per shop (Part D).

A version is written with the draft (``active_from`` empty while it is a draft),
starts when the listing goes live, and ends when the app replaces the text
(Replace images, full mode). The listing's analytics are compared by these
periods, so a change is never mixed into the numbers of the text before it.
Edits made directly on Etsy are not seen by the app.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ContentVersion, GeneratedContent, ListingPublication

SHORT, LONG = "short", "long"
REASONS = ("generated", "edited", "replaced")


def title_style(content: GeneratedContent | None) -> str:
    """"short" when the listing was written in the "Etsy recommended (short)" style."""
    return SHORT if content is not None and (content.attributes or {}).get("search") else LONG


def reason_for(content: GeneratedContent | None) -> str:
    return "edited" if content is not None and (content.attributes or {}).get("edited") else "generated"


def mark_edited(content: GeneratedContent) -> None:
    """The seller changed the text in the app: its versions say "edited"."""
    if not (content.attributes or {}).get("edited"):
        content.attributes = {**(content.attributes or {}), "edited": True}


async def current(session: AsyncSession, publication_id: Any) -> ContentVersion | None:
    return (await session.execute(
        select(ContentVersion)
        .where(ContentVersion.publication_id == publication_id, ContentVersion.active_to.is_(None))
        .order_by(ContentVersion.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


def drafted(
    session: AsyncSession, publication: ListingPublication, content: GeneratedContent, *,
    title: str | None, tags: list[str] | None, description: str | None, attributes: dict[str, str],
) -> ContentVersion:
    """The version a new draft carries (not live yet)."""
    version = ContentVersion(
        tenant_id=publication.tenant_id, connection_id=publication.connection_id, publication_id=publication.id,
        etsy_listing_id=publication.etsy_listing_id, title=title, tags=list(tags or []), description=description,
        attributes=dict(attributes), title_style=title_style(content), reason=reason_for(content),
    )
    session.add(version)
    return version


async def went_live(session: AsyncSession, publication: ListingPublication, at: datetime) -> None:
    version = await current(session, publication.id)
    if version is not None and version.active_from is None:
        version.active_from = at


async def replaced(
    session: AsyncSession, publication: ListingPublication, at: datetime, *,
    title: str | None, tags: list[str] | None, description: str | None, style: str = LONG,
) -> ContentVersion:
    """The app rewrote the listing's text on Etsy: the old version ends now."""
    before = await current(session, publication.id)
    if before is not None:
        before.active_to = at
    version = ContentVersion(
        tenant_id=publication.tenant_id, connection_id=publication.connection_id, publication_id=publication.id,
        etsy_listing_id=publication.etsy_listing_id, title=title, tags=list(tags or []), description=description,
        attributes=dict(before.attributes or {}) if before is not None else None, title_style=style,
        reason="replaced", active_from=at if publication.published_at is not None else None,
    )
    session.add(version)
    return version
