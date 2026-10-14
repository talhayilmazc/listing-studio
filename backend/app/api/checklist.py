"""What the search-readiness checklist (pipeline/checklist.py) is computed from,
read from what this app already stores.

The listing's text, its profile, the account's trademark list, the listing's
Occasion / Holiday / Section and its target shops' sections as the review card
shows them (api/item_options.py). Nothing is read from Etsy here and nothing is
queued: a shop whose sections are not stored (or older than 24 hours) is left
out of the Section part rather than read again.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.item_options import _property, _shop_out, target_shops_for
from app.api.shops import shop_label
from app.compliance.trademarks import blocklist_for_tenant, filter_on
from app.db.models import GeneratedContent, ListingProfile, Tenant
from app.pipeline import checklist, item_options as opts
from app.pipeline.content import bounds_for


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


def _options(
    content: GeneratedContent, profile: ListingProfile | None, shops: list[Any], names: dict[str, str]
) -> list[checklist.OptionState]:
    """Occasion, Holiday and each target shop's Section, as the card's Item Options shows them."""
    payload = (profile.cached_payload or {}) if profile is not None else {}
    out: list[checklist.OptionState] = []
    for name, default in (
        (opts.OCCASION, profile.default_occasion if profile else None),
        (opts.HOLIDAY, profile.default_holiday if profile else None),
    ):
        prop = _property(payload, content, name, default)
        if prop is not None:  # the category offers it
            out.append(checklist.OptionState(prop.name, list(prop.selected), decided=prop.source == "seller"))
    for shop in shops:
        section = _shop_out(content, shop, profile, names)
        if section.sections is None:
            continue  # not stored (or stale): not read from Etsy for this
        chose_none = section.source == "seller" and section.selected_id is None and section.missing_title is None
        out.append(
            checklist.OptionState(
                f"Section in {section.shop_name or 'your shop'}",
                [section.selected_title] if section.selected_title else [],
                decided=chose_none,
            )
        )
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
    targets = await target_shops_for(session, tenant_id, contents)
    out: dict[uuid.UUID, schemas.ChecklistOut] = {}
    for content in contents:
        profile = profiles.get(content.listing_profile_id) if content.listing_profile_id else None
        shops = targets.get(content.id, [])
        names = {str(s.id): shop_label(s) for s in shops}
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
            options=_options(content, profile, shops, names),
            profile=profile,
        )
        out[content.id] = checklist_out(result)
    return out
