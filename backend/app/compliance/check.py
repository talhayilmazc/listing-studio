"""Whether a listing's own text may go to Etsy now.

Shared by everything that sends a listing on: creating drafts, "Publish now"
and scheduled publishing (v6 §G), so they refuse for the same reasons.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.scanner import rescan
from app.compliance.trademarks import tenant_blocklist
from app.db.models import ComplianceFinding, ComplianceSeverity, GeneratedContent, ListingProfile
from app.pipeline.content import GeneratedListing, policy_for, validate_listing


async def blocking_finding(session: AsyncSession, content: GeneratedContent) -> str | None:
    """The first blocking compliance finding's detail, or None."""
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


async def listing_problem(session: AsyncSession, content: GeneratedContent) -> str | None:
    """Why this listing cannot go to any shop (its own text), or None.

    Scans again first: the trademark list may have grown since the listing was
    approved, and the job that publishes it reads these findings (v6 §B).
    """
    policy = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
    errors = validate_listing(
        GeneratedListing(
            title=content.title or "",
            tags=list(content.tags or []),
            description=content.description or "",
        ),
        policy,
        trademarks=await tenant_blocklist(session, content.tenant_id),
    )
    if errors:
        return "; ".join(errors)
    await rescan(session, content)
    blocked = await blocking_finding(session, content)
    if blocked:
        return f"compliance: {blocked}"
    return None
