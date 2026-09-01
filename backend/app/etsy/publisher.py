"""Publish approved content to Etsy as a DRAFT listing (spec §8).

Sequence (every HTTP call is gated by the client's quota + token bucket):

1. refuse if a ``blocking`` compliance finding exists,
2. resolve the shop (cache ``shop_id`` on the connection),
3. pick a shop section from the vision theme/occasion (rules first),
4. validate the size property against the taxonomy,
5. **createDraftListing** — created as a draft; ``state`` is NEVER set,
6. snapshot the created listing before any further write (rollback anchor),
7. updateListingInventory with size variations + SKU,
8. upload images in rank order: prepared thumbnail ``rank=1``, then the rest,
   with the optional size-chart image second-to-last,
9. record ``etsy_listing_id`` on the content.

Partial failure (e.g. an image upload) leaves the draft in place; the worker
records which step failed. Nothing is ever auto-published.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ComplianceFinding,
    ComplianceSeverity,
    EtsyConnection,
    GeneratedContent,
    ListingSnapshot,
)
from app.etsy.api import EtsyApiClient
from app.pipeline.attributes import resolve_required_attributes
from app.pipeline.reference import build_inventory_from_reference
from app.pipeline.sections import choose_section

logger = logging.getLogger(__name__)


class PublishBlocked(Exception):
    """A blocking compliance finding prevents publishing this content."""


@dataclass
class PublishImage:
    data: bytes
    filename: str
    mime_type: str = "image/jpeg"
    rank: int = 0


@dataclass
class PublishConfig:
    #: Stock quantity for made-to-order products (a fulfilment setting, not listing
    #: metadata). Everything else -- category, price, who_made, when_made, shipping,
    #: return policy, production, auto-renew -- comes from the reference (v4 §0/§C).
    quantity: int


@dataclass
class PublishResult:
    listing_id: int
    listing_url: str
    section_id: int | None = None
    sizes_applied: bool = False
    image_count: int = 0


@dataclass
class ReplaceResult:
    listing_id: int
    deleted: int
    added: int
    kept: int


def listing_url(listing_id: int) -> str:
    """The public Etsy listing URL (ToU back-link requirement); active listings only."""
    return f"https://www.etsy.com/listing/{listing_id}"


def listing_edit_url(listing_id: int) -> str:
    """Shop Manager editor URL for a DRAFT listing.

    A draft has no working public URL ("Sorry this item is unavailable"), so the UI
    links drafts here instead. Public :func:`listing_url` is used once active.
    """
    return f"https://www.etsy.com/your/shops/me/listing-editor/edit/{listing_id}"


def link_for(listing_id: int, state: str | None) -> str:
    """Pick the edit URL for a draft (default) or the public URL for an active listing."""
    return listing_url(listing_id) if state == "active" else listing_edit_url(listing_id)


def _first_shop(resp: dict[str, Any]) -> dict[str, Any]:
    if resp.get("results"):
        return resp["results"][0]
    if "shop_id" in resp:
        return resp
    raise ValueError("no shop found for this Etsy account")


def _rank(images: list[PublishImage]) -> list[PublishImage]:
    """Assign 1-based ranks in list order (thumbnail first)."""
    for i, image in enumerate(images, start=1):
        image.rank = i
    return images


async def _has_blocking_finding(session: AsyncSession, content_id: uuid.UUID) -> bool:
    rows = await session.execute(
        select(ComplianceFinding.id).where(
            ComplianceFinding.generated_content_id == content_id,
            ComplianceFinding.severity == ComplianceSeverity.blocking,
        )
    )
    return rows.first() is not None


async def publish_content(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    content: GeneratedContent,
    connection: EtsyConnection,
    sku: str | None,
    thumbnail: PublishImage,
    client: EtsyApiClient,
    access_token: str,
    config: PublishConfig,
    reference: dict[str, Any],
    extra_images: list[PublishImage] | None = None,
    fixed_image_ids: list[int] | None = None,
    theme: str = "",
    occasion: str = "",
    vision: dict[str, Any] | None = None,
    profile_name: str = "",
    auto_create_sections: bool = False,
    tenant_limit: int,
) -> PublishResult:
    extras = list(extra_images) if extra_images else []
    fixed = list(fixed_image_ids or [])
    tenant_id = connection.tenant_id
    ctx = {"access_token": access_token, "tenant_id": tenant_id, "tenant_limit": tenant_limit}

    # 1) Compliance gate.
    if await _has_blocking_finding(session, content.id):
        raise PublishBlocked("content has a blocking compliance finding")

    # 2) Resolve the shop id.
    if connection.shop_id is None:
        if connection.etsy_user_id is None:
            raise ValueError("connection has no Etsy user id")
        shop = _first_shop(await client.get_shop_by_owner_user_id(connection.etsy_user_id, **ctx))
        connection.shop_id = int(shop["shop_id"])
        connection.shop_name = shop.get("shop_name") or connection.shop_name
        await session.commit()
    shop_id = connection.shop_id

    # 3) Choose a shop section (v3 §F): a Comfort Colors profile ALWAYS maps to the
    # shop's "Comfort Colors" section (deterministic, no LLM); otherwise match by
    # theme (rules.json). Every branch is logged so a missing section is diagnosable.
    sections_resp = await client.get_shop_sections(shop_id, **ctx)
    section_by_title = {
        str(s["title"]): int(s["shop_section_id"]) for s in sections_resp.get("results", [])
    }
    section_by_lower = {title.lower(): sid for title, sid in section_by_title.items()}

    section_id: int | None = None
    if "comfort colors" in profile_name.lower():
        section_id = section_by_lower.get("comfort colors")
        if section_id is not None:
            logger.info("section: Comfort Colors profile rule -> section %s", section_id)
        else:
            logger.warning(
                "section: Comfort Colors profile %r but shop has no 'Comfort Colors' "
                "section; leaving unset (existing: %s)",
                profile_name,
                list(section_by_title),
            )
    else:
        decision = choose_section(
            theme=theme,
            occasion=occasion,
            existing_sections=list(section_by_title),
            auto_create=auto_create_sections,
        )
        if decision.name and decision.exists:
            section_id = section_by_title[decision.name]
            logger.info("section: theme rule matched existing '%s' (%s)", decision.name, section_id)
        elif decision.name and decision.create:
            created = await client.create_shop_section(shop_id, title=decision.name, **ctx)
            section_id = int(created["shop_section_id"])
            logger.info("section: theme rule created '%s' (%s)", decision.name, section_id)
        elif decision.name:
            logger.info(
                "section: theme rule matched '%s' but shop has no such section and "
                "auto-create is off; leaving unset",
                decision.name,
            )
        else:
            logger.info(
                "section: no rule matched (theme=%r occasion=%r); leaving unset", theme, occasion
            )

    logger.info(
        "section: %s",
        f"shop_section_id {section_id} will be sent" if section_id else "no section on the draft",
    )

    # 4) Create the DRAFT listing. Every field is copied VERBATIM from the reference
    # -- never re-selected or defaulted (v4 §0/§A). These are Etsy's mandatory fields
    # for a PHYSICAL listing (audited against createDraftListing): the base fields
    # plus shipping_profile_id, return_policy_id and readiness_state_id (the modern
    # processing profile that replaces raw processing_min/max). A valid reference
    # listing has them all; if any is missing we fail with the list rather than
    # guess a default.
    required = {
        "taxonomy_id": reference.get("taxonomy_id"),
        "price": reference.get("price"),
        "who_made": reference.get("who_made"),
        "when_made": reference.get("when_made"),
        "shipping_profile_id": reference.get("shipping_profile_id"),
        "return_policy_id": reference.get("return_policy_id"),
        "readiness_state_id": reference.get("readiness_state_id"),
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        raise ValueError(
            "reference profile is missing required physical-listing fields: "
            + ", ".join(missing)
            + "; refresh the profile before publishing"
        )
    taxonomy_id = required["taxonomy_id"]
    listing: dict[str, Any] = {
        "quantity": config.quantity,
        "title": content.title or "",
        "description": content.description or "",
        # Apparel is always a PHYSICAL listing. Sending type=download makes Etsy
        # create a digital listing and force the "Digital files" category (v4 §A).
        "type": "physical",
        "tags": list(content.tags or []),
        **required,
    }
    # production_partner_ids: required only for made-by-someone-else; copy if present.
    if reference.get("production_partner_ids"):
        listing["production_partner_ids"] = reference["production_partner_ids"]
    # Tri-state flags: include when explicitly set (False is meaningful, don't drop).
    for key in ("is_supply", "is_customizable", "is_personalizable", "should_auto_renew"):
        if reference.get(key) is not None:
            listing[key] = reference[key]
    if section_id is not None:
        listing["shop_section_id"] = section_id
    created = await client.create_draft_listing(shop_id, listing=listing, **ctx)
    listing_id = int(created["listing_id"])

    # 5) Snapshot the created baseline before any further write.
    session.add(
        ListingSnapshot(
            tenant_id=tenant_id,
            listing_id=listing_id,
            job_id=job_id,
            payload={"operation": "create_draft", "submitted": listing, "created": created},
        )
    )
    await session.commit()

    # 5a) Read the draft back and verify Etsy stored the reference category. A wrong
    # `type` or a re-selected category lands it under Digital; fail loudly (v4 §A).
    readback = await client.get_listing(listing_id, **ctx)
    stored_taxonomy = readback.get("taxonomy_id")
    if int(stored_taxonomy or 0) != int(taxonomy_id):
        raise ValueError(
            f"draft taxonomy_id {stored_taxonomy} does not match reference {taxonomy_id}; "
            "the listing would be in the wrong category"
        )

    # 5b) Required category attributes (neckline, sleeve length, clothing style, ...):
    # copy from the reference, else derive from the mockup vision, else fail with the
    # missing names -- never a hardcoded default (v4 §B). Missing required attributes
    # are why "save then publish" was needed in Etsy's UI.
    props = await client.get_properties_by_taxonomy_id(taxonomy_id, **ctx)
    resolved, missing = resolve_required_attributes(
        props.get("results", []), reference.get("attributes"), vision
    )
    if missing:
        raise ValueError(
            "required clothing attributes could not be determined: " + ", ".join(missing)
        )
    for attr in resolved:
        await client.update_listing_property(
            shop_id,
            listing_id,
            attr.property_id,
            value_ids=attr.value_ids,
            values=attr.values,
            scale_id=attr.scale_id,
            **ctx,
        )

    # 6) Inventory: the reference variation structure with OUR sku on every product.
    inventory = build_inventory_from_reference(
        reference.get("inventory_products") or [],
        sku=sku,
        quantity=config.quantity,
        fallback_price=reference.get("price"),
        readiness_state_id=reference.get("readiness_state_id"),
        price_on_property=reference.get("price_on_property"),
        quantity_on_property=reference.get("quantity_on_property"),
        sku_on_property=reference.get("sku_on_property"),
    )
    await client.update_listing_inventory(listing_id, inventory=inventory, **ctx)
    has_variations = len(inventory["products"]) > 1

    # 7) Upload new images (thumbnail rank 1, then siblings), then re-use the
    # reference's fixed images (B3, e.g. size charts) by id, in order.
    ordered = _rank([thumbnail, *extras])
    for image in ordered:
        await client.upload_listing_image(
            shop_id,
            listing_id,
            image_bytes=image.data,
            filename=image.filename,
            rank=image.rank,
            mime_type=image.mime_type,
            **ctx,
        )
    next_rank = len(ordered)
    for image_id in fixed:
        next_rank += 1
        await client.upload_listing_image(
            shop_id, listing_id, listing_image_id=image_id, rank=next_rank, **ctx
        )

    # 8) Record the listing id. It is created as a DRAFT (state never set), so mark
    # it as such -- the UI links a draft to Shop Manager, not the public URL (A4).
    content.etsy_listing_id = listing_id
    content.etsy_listing_state = "draft"
    await session.commit()

    return PublishResult(
        listing_id=listing_id,
        listing_url=listing_edit_url(listing_id),  # draft -> Shop Manager (A4)
        section_id=section_id,
        sizes_applied=has_variations,
        image_count=next_rank,
    )


async def publish_live(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    content: GeneratedContent,
    connection: EtsyConnection,
    client: EtsyApiClient,
    access_token: str,
    tenant_limit: int,
) -> PublishResult:
    """Make an existing DRAFT listing ACTIVE (the explicit "Publish now" step, E).

    Publishing is never automatic: this runs only from a deliberate user action on a
    draft the seller has already reviewed and approved. A blocking compliance finding
    still prevents it.
    """
    tenant_id = connection.tenant_id
    ctx = {"access_token": access_token, "tenant_id": tenant_id, "tenant_limit": tenant_limit}

    if await _has_blocking_finding(session, content.id):
        raise PublishBlocked("content has a blocking compliance finding")
    if content.etsy_listing_id is None:
        raise ValueError("no draft listing to publish; create the draft first")

    # updateListing is shop-scoped, so resolve (and cache) the shop id.
    if connection.shop_id is None:
        if connection.etsy_user_id is None:
            raise ValueError("connection has no Etsy user id")
        shop = _first_shop(await client.get_shop_by_owner_user_id(connection.etsy_user_id, **ctx))
        connection.shop_id = int(shop["shop_id"])
        await session.commit()
    shop_id = connection.shop_id
    listing_id = content.etsy_listing_id

    # Snapshot the pre-change state so the go-live can be rolled back to draft.
    session.add(
        ListingSnapshot(
            tenant_id=tenant_id,
            listing_id=listing_id,
            job_id=job_id,
            payload={"operation": "publish_live", "previous_state": content.etsy_listing_state or "draft"},
        )
    )
    await session.commit()

    await client.update_listing(shop_id, listing_id, updates={"state": "active"}, **ctx)
    content.etsy_listing_state = "active"
    await session.commit()

    return PublishResult(
        listing_id=listing_id,
        listing_url=listing_url(listing_id),  # active -> public URL (ToU back-link)
    )


async def replace_listing_images(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    listing_id: int,
    shop_id: int,
    tenant_id: uuid.UUID,
    client: EtsyApiClient,
    access_token: str,
    tenant_limit: int,
    existing_listing: dict[str, Any],
    keep_image_ids: list[int],
    delete_image_ids: list[int],
    new_images: list[PublishImage],
    new_title: str,
    new_tags: list[str],
    new_description: str,
) -> ReplaceResult:
    """Update an existing listing in place: swap artwork images + refresh copy (B4).

    Deletes only the artwork images (size charts are retained and re-ranked after the
    new photos), uploads the new photos in order, and updates title/13-tags/description
    — the title block only. Category, price, variations, shipping, partners, section
    and **state** are never touched. Snapshots the listing before any write.
    """
    ctx = {"access_token": access_token, "tenant_id": tenant_id, "tenant_limit": tenant_limit}

    # 1) Snapshot the whole listing BEFORE any write (rollback anchor).
    session.add(
        ListingSnapshot(
            tenant_id=tenant_id,
            listing_id=listing_id,
            job_id=job_id,
            payload={"operation": "replace_images", "listing": existing_listing},
        )
    )
    await session.commit()

    # 2) Delete only the artwork images; the size charts stay.
    for image_id in delete_image_ids:
        await client.delete_listing_image(shop_id, listing_id, image_id, **ctx)

    # 3) Upload the new photos first (thumbnail rank 1), ...
    ranked = _rank(list(new_images))
    for image in ranked:
        await client.upload_listing_image(
            shop_id,
            listing_id,
            image_bytes=image.data,
            filename=image.filename,
            rank=image.rank,
            mime_type=image.mime_type,
            **ctx,
        )
    # 4) ... then re-rank the retained size charts to come after them.
    rank = len(ranked)
    for image_id in keep_image_ids:
        rank += 1
        await client.upload_listing_image(
            shop_id, listing_id, listing_image_id=image_id, rank=rank, **ctx
        )

    # 5) Refresh only the copy; NEVER touch state, price, taxonomy, variations, etc.
    await client.update_listing(
        shop_id,
        listing_id,
        updates={"title": new_title, "description": new_description, "tags": new_tags},
        **ctx,
    )

    return ReplaceResult(
        listing_id=listing_id,
        deleted=len(delete_image_ids),
        added=len(ranked),
        kept=len(keep_image_ids),
    )
