"""Whether a listing's own text may go to Etsy now.

Shared by everything that sends a listing on: creating drafts, "Publish now"
and scheduled publishing (v6 §G), so they refuse for the same reasons.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.scanner import rescan
from app.compliance.trademarks import tenant_blocklist
from app.db.models import ComplianceFinding, ComplianceSeverity, GeneratedContent, ListingProfile
from app.pipeline import manual, personalization
from app.pipeline.content import GeneratedListing, bounds_for, check_listing, policy_for


async def prefetch_blocking(session: AsyncSession, contents: list[GeneratedContent]) -> None:
    """For a page only showing these listings (no scan in between): their blocking findings in
    one query, answered by :func:`blocking_finding` for the rest of the session's reads."""
    ids = [c.id for c in contents]
    found: dict = {i: None for i in ids}
    for cid, detail in (await session.execute(select(ComplianceFinding.generated_content_id, ComplianceFinding.detail).where(
        ComplianceFinding.generated_content_id.in_(ids), ComplianceFinding.severity == ComplianceSeverity.blocking,
    ))).all() if ids else []:
        if found.get(cid) is None:
            found[cid] = detail or "blocking compliance finding"
    session.sync_session.info.setdefault("blocking", {}).update(found)


async def blocking_finding(session: AsyncSession, content: GeneratedContent) -> str | None:
    """The first blocking compliance finding's detail, or None."""
    known = session.sync_session.info.get("blocking")
    if known is not None and content.id in known:
        return known[content.id]
    rows = await session.execute(
        select(ComplianceFinding.detail).where(
            ComplianceFinding.generated_content_id == content.id,
            ComplianceFinding.severity == ComplianceSeverity.blocking,
        )
    )
    row = rows.first()
    if row is None:
        return None
    return row[0] or "blocking compliance finding"


async def listing_problem(session: AsyncSession, content: GeneratedContent, shop_id: Any = None, *,
                          scan: bool = True) -> str | None:
    """Why this listing cannot go to any shop (its own text), or None. ``shop_id``: its text
    as it goes to that shop (pipeline/shop_listing.py::text_for: the shop's own, else the
    listing's).

    Scans again first: the trademark list may have grown since the listing was
    approved, and the job that publishes it reads these findings (v6 §B). ``scan=False``: a
    page only showing it reads the findings as they are (sending and drafting scan again).
    """
    policy = None
    profile = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
    # The same decision as approval: a listing the seller wrote is stopped only by
    # what Etsy refuses and the trademark filter (content.check_listing).
    from app.pipeline import shop_listing

    title, tags, description = shop_listing.text_for(content, shop_id)
    errors = check_listing(
        GeneratedListing(title=title, tags=tags, description=description),
        policy,
        hand_written=manual.is_manual(content),
        trademarks=await tenant_blocklist(session, content.tenant_id),
        title_rules=bounds_for(profile),
        for_seller=True,
    ).errors
    if errors:
        return "; ".join(errors)
    if scan:
        session.sync_session.info.get("blocking", {}).pop(content.id, None)  # scanned again: read it again
        await rescan(session, content)
    blocked = await blocking_finding(session, content)
    if blocked:
        return f"compliance: {blocked}"
    return None


async def personalization_setting(session: AsyncSession, content: GeneratedContent) -> tuple[dict | None, str]:
    """The personalization a new draft of this listing would get, and where it comes from
    ("listing", "profile", "unknown"): pipeline/personalization.py."""
    profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
    if profile is not None and profile.tenant_id != content.tenant_id:
        profile = None
    return personalization.for_listing(
        content.personalization, profile.personalization if profile else None, profile.cached_payload if profile else None
    )


async def personalization_hold(session: AsyncSession, content: GeneratedContent) -> str | None:
    """Why no new draft can be made for this listing because of its personalization (Etsy
    would refuse the question), with the fix in plain words; None when it can go.

    Only new drafts are held: a draft that exists already went to Etsy with a question Etsy
    accepted, so going live never waits on this (workers/schedule.py does not ask it)."""
    setting, _ = await personalization_setting(session, content)
    return personalization.held_reason(setting)
