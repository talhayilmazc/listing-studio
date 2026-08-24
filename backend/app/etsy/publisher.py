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
from app.pipeline.sections import choose_section
from app.pipeline.sizes import SizeConfig, build_inventory, taxonomy_supports_property


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
    default_taxonomy_id: int
    price: float
    quantity: int
    currency: str
    section_title: str
    who_made: str
    when_made: str
    listing_type: str


@dataclass
class PublishResult:
    listing_id: int
    listing_url: str
    section_id: int | None = None
    sizes_applied: bool = False
    image_count: int = 0


def listing_url(listing_id: int) -> str:
    """The public Etsy listing URL (ToU back-link requirement)."""
    return f"https://www.etsy.com/listing/{listing_id}"


def _first_shop(resp: dict[str, Any]) -> dict[str, Any]:
    if resp.get("results"):
        return resp["results"][0]
    if "shop_id" in resp:
        return resp
    raise ValueError("no shop found for this Etsy account")


def _order_images(
    thumbnail: PublishImage,
    extras: list[PublishImage],
    size_chart: PublishImage | None,
) -> list[PublishImage]:
    """Thumbnail first; size chart second-to-last; assign 1-based ranks."""
    ordered = [thumbnail, *extras]
    if size_chart is not None:
        ordered.insert(max(len(ordered) - 1, 1), size_chart)
    for i, image in enumerate(ordered, start=1):
        image.rank = i
    return ordered


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
    extra_images: list[PublishImage] | None = None,
    size_chart: PublishImage | None = None,
    size_config: SizeConfig | None = None,
    theme: str = "",
    occasion: str = "",
    auto_create_sections: bool = False,
    tenant_limit: int,
) -> PublishResult:
    extras = list(extra_images) if extra_images else []
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

    # 3) Choose a shop section from the theme/occasion.
    sections_resp = await client.get_shop_sections(shop_id, **ctx)
    section_by_title = {
        str(s["title"]): int(s["shop_section_id"]) for s in sections_resp.get("results", [])
    }
    decision = choose_section(
        theme=theme,
        occasion=occasion,
        existing_sections=list(section_by_title),
        auto_create=auto_create_sections,
    )
    section_id: int | None = None
    if decision.name and decision.exists:
        section_id = section_by_title[decision.name]
    elif decision.name and decision.create:
        created = await client.create_shop_section(shop_id, title=decision.name, **ctx)
        section_id = int(created["shop_section_id"])

    # 4) Validate size support against the taxonomy.
    taxonomy_id = content.taxonomy_id or config.default_taxonomy_id
    supports_sizes = False
    if size_config is not None and size_config.values:
        props = await client.get_properties_by_taxonomy_id(taxonomy_id, **ctx)
        supports_sizes = taxonomy_supports_property(props, size_config.property)

    # 5) Create the DRAFT listing (state is never set -> stays a draft).
    listing: dict[str, Any] = {
        "quantity": config.quantity,
        "title": content.title or "",
        "description": content.description or "",
        "price": config.price,
        "who_made": config.who_made,
        "when_made": config.when_made,
        "taxonomy_id": taxonomy_id,
        "type": config.listing_type,
        "tags": list(content.tags or []),
    }
    if section_id is not None:
        listing["shop_section_id"] = section_id
    created = await client.create_draft_listing(shop_id, listing=listing, **ctx)
    listing_id = int(created["listing_id"])

    # 6) Snapshot the created baseline before any further write.
    session.add(
        ListingSnapshot(
            tenant_id=tenant_id,
            listing_id=listing_id,
            job_id=job_id,
            payload={"operation": "create_draft", "submitted": listing, "created": created},
        )
    )
    await session.commit()

    # 7) Inventory: sizes (if supported) + SKU.
    inventory = build_inventory(
        sku=sku,
        base_price=config.price,
        quantity=config.quantity,
        size_config=size_config if supports_sizes else None,
    )
    await client.update_listing_inventory(listing_id, inventory=inventory, **ctx)

    # 8) Upload images in rank order.
    ordered = _order_images(thumbnail, extras, size_chart)
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

    # 9) Record the listing id.
    content.etsy_listing_id = listing_id
    await session.commit()

    return PublishResult(
        listing_id=listing_id,
        listing_url=listing_url(listing_id),
        section_id=section_id,
        sizes_applied=supports_sizes,
        image_count=len(ordered),
    )
