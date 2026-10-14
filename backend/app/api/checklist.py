"""What the search-readiness checklist (pipeline/checklist.py) is computed from,
read from what this app already stores.

The listing's text, its profile (its style, bounds, prefix, Occasion / Holiday
defaults and category lists), the listing's Occasion / Holiday as the review
card shows them (api/item_options.py) and the account's trademark list. Nothing
is read from Etsy here and nothing is queued.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.item_options import _property
from app.compliance.trademarks import blocklist_for_tenant, filter_on
from app.db.models import GeneratedContent, ListingProfile, Tenant
from app.pipeline import checklist, manual
from app.pipeline import item_options as opts
from app.pipeline.content import bounds_for, uses_search_style


def checklist_out(result: checklist.Checklist) -> schemas.ChecklistOut:
    return schemas.ChecklistOut(
        version=result.version,
        heading=checklist.HEADING,
        note=checklist.NOTE,
        summary=result.summary,
        passed=result.passed,
        total=len(result.checks),
        checks=[
            schemas.CheckOut(key=c.key, label=c.label, status=c.status, reason=c.reason, source=c.source)
            for c in result.checks
        ],
    )


def _options(content: GeneratedContent, profile: ListingProfile | None) -> list[checklist.OptionState]:
    """Occasion and Holiday, when the category offers them, and who decided each:
    the seller on the card, else the app's writer (it chose from the design, so an
    empty one is its decision), else the profile's default; a listing the seller
    writes with no default for it is undecided."""
    payload = (profile.cached_payload or {}) if profile is not None else {}
    written = not manual.is_manual(content)
    out: list[checklist.OptionState] = []
    for name, default in (
        (opts.OCCASION, profile.default_occasion if profile else None),
        (opts.HOLIDAY, profile.default_holiday if profile else None),
    ):
        prop = _property(payload, content, name, default)
        if prop is None:  # the category does not offer it
            continue
        chosen_by = "you" if prop.source == "seller" else "writer" if written else "profile" if default is not None else ""
        out.append(checklist.OptionState(prop.name, list(prop.selected), chosen_by=chosen_by))
    return out


async def checklists_for(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contents: list[GeneratedContent],
    profiles: dict[uuid.UUID, ListingProfile],
    *,
    edits: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> dict[uuid.UUID, schemas.ChecklistOut]:
    """Each listing's checklist; ``edits`` holds unsaved text (title, tags, description)."""
    if not contents:
        return {}
    tenant = await session.get(Tenant, tenant_id)
    blocklist = blocklist_for_tenant(tenant)
    on = filter_on(tenant)
    out: dict[uuid.UUID, schemas.ChecklistOut] = {}
    for content in contents:
        profile = profiles.get(content.listing_profile_id) if content.listing_profile_id else None
        unsaved = (edits or {}).get(content.id, {})
        vision = (content.attributes or {}).get("vision") or {}
        result = checklist.build(
            unsaved.get("title", content.title or ""),
            list(unsaved.get("tags", content.tags or [])),
            unsaved.get("description", content.description or ""),
            title_rules=bounds_for(profile),
            prefix=profile.title_prefix if profile is not None else None,
            blocklist=blocklist,
            filter_on=on,
            characters=[str(c) for c in vision.get("characters") or []],
            options=_options(content, profile),
            profile=profile,
            # The style the writer uses for this profile (content.uses_search_style).
            search_style=profile is not None and uses_search_style(profile),
        )
        out[content.id] = checklist_out(result)
    return out
