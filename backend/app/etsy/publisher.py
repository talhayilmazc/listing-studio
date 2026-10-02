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
9. record the draft as a :class:`ListingPublication` for this content in this shop
   (one content can have a draft in each of several shops, v5 §E).

Creating a draft is **resumable**. A :class:`DraftAttempt` is written before
``createDraftListing`` and holds the listing id from the moment Etsy returns it.
If anything after that fails (a timeout, a 429 that outlasted its retries, the
worker restarting), the next try reads that listing back and carries on from
what it already has: the settings are simply set again (they are idempotent),
and only the images Etsy does not have yet are uploaded. A create request whose
answer never arrived is looked for among the shop's own newest drafts by its
title before another is sent. So trying again never makes a second draft. The
attempt is deleted when the publication is recorded. Nothing is ever
auto-published.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import allowance
from app.db.models import (
    ComplianceFinding,
    ComplianceSeverity,
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    ListingPublication,
    ListingSnapshot,
)
from app.etsy.api import EtsyApiClient
from app.etsy.errors import EtsyClientError, EtsyServerError
from app.pipeline.attributes import resolve_required_attributes
from app.pipeline.personalization import questions_for
from app.pipeline.reference import (
    PAYLOAD_VERSION,
    build_inventory_from_reference,
    production_partner_ids,
)
from app.pipeline.sections import choose_section

logger = logging.getLogger(__name__)


class PublishBlocked(Exception):
    """A blocking compliance finding prevents publishing this content."""


class NotYet(Exception):
    """Nothing is wrong with the listing; this try could not finish. The worker
    runs it again after ``seconds`` and it carries on where it stopped."""

    def __init__(self, message: str, *, seconds: float | None = None) -> None:
        super().__init__(message)
        self.seconds = NOT_YET_SECONDS if seconds is None else seconds


#: How long the worker waits before the next try after a :class:`NotYet`.
NOT_YET_SECONDS = 60.0
#: No answer, or Etsy's own error: the request may or may not have taken effect.
UNCERTAIN = (httpx.TransportError, EtsyServerError)
#: After an unanswered create, how long Etsy gets before its drafts are searched,
#: and how long a create must be unaccounted for before another is sent.
ADOPT_WAIT_SECONDS = 5.0
RECREATE_AFTER_SECONDS = 90.0
#: Sends of one image before the try gives up (each checked against Etsy first).
UPLOAD_TRIES = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _same_title(a: str | None, b: str | None) -> bool:
    """Titles compared as Etsy returns them: entities decoded, whitespace collapsed."""
    norm = lambda t: " ".join(html.unescape(t or "").split()).casefold()  # noqa: E731
    return bool(a) and norm(a) == norm(b)


async def attempt_for(session: AsyncSession, content_id: uuid.UUID, connection_id: uuid.UUID) -> DraftAttempt | None:
    rows = await session.execute(
        select(DraftAttempt).where(DraftAttempt.content_id == content_id, DraftAttempt.connection_id == connection_id)
    )
    return rows.scalar_one_or_none()


async def _find_unanswered_draft(
    session: AsyncSession, client: EtsyApiClient, shop_id: int, attempt: DraftAttempt, ctx: dict[str, Any]
) -> int | None:
    """The draft a lost create request made, if Etsy made it: the newest of the
    shop's own drafts with the title that was sent, created since it was sent,
    that the app does not already hold. The seller's own shop only."""
    if not attempt.title or attempt.create_sent_at is None:
        return None
    sent = attempt.create_sent_at if attempt.create_sent_at.tzinfo else attempt.create_sent_at.replace(tzinfo=timezone.utc)
    drafts = await client.get_listings_by_shop(shop_id, state="draft", limit=25, **ctx)
    known = set((await session.execute(
        select(ListingPublication.etsy_listing_id).where(ListingPublication.connection_id == attempt.connection_id)
    )).scalars())
    known |= set((await session.execute(
        select(DraftAttempt.etsy_listing_id).where(
            DraftAttempt.connection_id == attempt.connection_id, DraftAttempt.etsy_listing_id.is_not(None))
    )).scalars())
    for row in sorted(drafts.get("results") or [], key=lambda r: int(r.get("listing_id") or 0), reverse=True):
        made = row.get("original_creation_timestamp") or row.get("created_timestamp") or row.get("creation_timestamp")
        if made is not None and int(made) < sent.timestamp() - 120:
            continue
        if _same_title(row.get("title"), attempt.title) and int(row["listing_id"]) not in known:
            return int(row["listing_id"])
    return None


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


async def _became_active(
    client: EtsyApiClient, listing_id: int, ctx: dict[str, Any], *, attempts: int, wait: float
) -> bool:
    """Read the listing back until it shows active, a few times, spaced out."""
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(wait)
        try:
            listing = await client.get_listing(listing_id, **ctx)
        except (httpx.TransportError, EtsyServerError):
            continue
        if listing.get("state") == "active":
            return True
    return False


async def publication_for(
    session: AsyncSession, content_id: uuid.UUID, connection_id: uuid.UUID
) -> ListingPublication | None:
    rows = await session.execute(
        select(ListingPublication).where(
            ListingPublication.content_id == content_id,
            ListingPublication.connection_id == connection_id,
        )
    )
    return rows.scalar_one_or_none()


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
    profile_id: uuid.UUID | None = None,
    title: str | None = None,
    description: str | None = None,
    personalization: dict[str, Any] | None = None,
) -> PublishResult:
    """Create the draft in ``connection``'s shop from ``reference`` (that shop's profile).

    ``personalization`` is the profile's effective setting (pipeline/personalization.py):
    when enabled, the draft gets that question, verified on read-back.

    ``title`` / ``description`` override the content's own when the draft goes to
    a shop other than the one the content was written for: the title carries that
    shop's prefix, the description that shop's reference body (v5 §E).
    """
    extras = list(extra_images) if extra_images else []
    fixed = list(fixed_image_ids or [])
    tenant_id = connection.tenant_id
    ctx = {"access_token": access_token, "tenant_id": tenant_id, "tenant_limit": tenant_limit}

    # 1) Compliance gate, and one draft per content per shop.
    if await _has_blocking_finding(session, content.id):
        raise PublishBlocked("content has a blocking compliance finding")
    if await publication_for(session, content.id, connection.id) is not None:
        raise ValueError("this listing already has a draft in this shop")
    attempt = await attempt_for(session, content.id, connection.id)

    # 2) Resolve the shop id.
    if connection.shop_id is None:
        if connection.etsy_user_id is None:
            raise ValueError("connection has no Etsy user id")
        shop = _first_shop(await client.get_shop_by_owner_user_id(connection.etsy_user_id, **ctx))
        connection.shop_id = int(shop["shop_id"])
        connection.shop_name = shop.get("shop_name") or connection.shop_name
        await session.commit()
    shop_id = connection.shop_id

    # 2a) An earlier try of this draft: carry on with its listing rather than
    # making another. The read-back that verifies a new draft doubles as the check
    # that the listing is still there (the seller may have deleted it since).
    listing_id: int | None = None
    readback: dict[str, Any] | None = None
    if attempt is not None and attempt.etsy_listing_id is None and attempt.create_sent_at is not None:
        found = await _find_unanswered_draft(session, client, shop_id, attempt, ctx)
        if found is not None:
            logger.info("draft: found listing %s from a create whose answer was lost", found)
            attempt.etsy_listing_id = found
            await session.commit()
        else:
            sent = attempt.create_sent_at if attempt.create_sent_at.tzinfo else attempt.create_sent_at.replace(tzinfo=timezone.utc)
            if _now() - sent < timedelta(seconds=RECREATE_AFTER_SECONDS):
                raise NotYet("checking whether Etsy created the draft before sending it again")
    if attempt is not None and attempt.etsy_listing_id is not None:
        try:
            readback = await client.get_listing(attempt.etsy_listing_id, **ctx)
            listing_id = attempt.etsy_listing_id
        except EtsyClientError as exc:
            if exc.status_code not in (404, 410):
                raise
            logger.info("draft: listing %s of an earlier try is gone; starting again", attempt.etsy_listing_id)
            attempt.etsy_listing_id, attempt.create_sent_at = None, None
            await session.commit()
        else:
            if readback.get("state") not in (None, "draft"):
                # No longer a draft (published or removed by hand): not ours to continue.
                attempt.etsy_listing_id, attempt.create_sent_at = None, None
                await session.commit()
                listing_id, readback = None, None
    resumed = listing_id is not None

    # 3) Choose a shop section (v3 §F): a Comfort Colors profile ALWAYS maps to the
    # shop's "Comfort Colors" section (deterministic, no LLM); otherwise match by
    # theme (rules.json). Every branch is logged so a missing section is diagnosable.
    # A draft being resumed already has its section.
    sections_resp = {"results": []} if resumed else await client.get_shop_sections(shop_id, **ctx)
    section_by_title = {
        str(s["title"]): int(s["shop_section_id"]) for s in sections_resp.get("results", [])
    }
    section_by_lower = {title.lower(): sid for title, sid in section_by_title.items()}

    section_id: int | None = None
    if resumed:
        section_id = (readback or {}).get("shop_section_id")
    elif "comfort colors" in profile_name.lower():
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
    if int(reference.get("payload_version") or 1) < PAYLOAD_VERSION:
        # Read before partners were copied: it would say "no partners" even when the
        # reference has them, and the draft would quietly lose them (v5 §D).
        raise ValueError(
            "the reference profile was read by an older version that did not copy "
            "production partners; refresh the profile, then try again"
        )
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
        "title": (title if title is not None else content.title) or "",
        "description": (description if description is not None else content.description) or "",
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
    # is_personalizable is not a createDraftListing field any more: personalization
    # is set through its own endpoint below (v7 §D4).
    for key in ("is_supply", "is_customizable", "should_auto_renew"):
        if reference.get(key) is not None:
            listing[key] = reference[key]
    if section_id is not None:
        listing["shop_section_id"] = section_id
    if listing_id is None:
        # The attempt is on record before the request goes out, so whatever
        # happens next, the following try knows a create was sent and with what title.
        if attempt is None:
            attempt = DraftAttempt(tenant_id=tenant_id, content_id=content.id, connection_id=connection.id)
            session.add(attempt)
        attempt.title, attempt.create_sent_at = listing["title"], _now()
        await session.commit()
        try:
            created = await client.create_draft_listing(shop_id, listing=listing, **ctx)
            listing_id = int(created["listing_id"])
        except UNCERTAIN as exc:
            # No answer, or a 5xx: Etsy may have made the draft anyway. Look for it.
            await asyncio.sleep(ADOPT_WAIT_SECONDS)
            found = await _find_unanswered_draft(session, client, shop_id, attempt, ctx)
            if found is None:
                raise NotYet("Etsy did not answer when the draft was sent; checking before sending it again") from exc
            logger.info("draft: create answered late; listing %s found among the shop's drafts", found)
            listing_id, created = found, {"listing_id": found}
        attempt.etsy_listing_id = listing_id

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
    elif not _same_title(attempt.title, listing["title"]):
        # The text was edited between tries: the draft takes the current text.
        await client.update_listing(
            shop_id, listing_id,
            updates={"title": listing["title"], "description": listing["description"], "tags": listing["tags"]}, **ctx,
        )
        attempt.title = listing["title"]
        await session.commit()
        readback = None

    # 5a) Read the draft back and verify Etsy stored the reference category. A wrong
    # `type` or a re-selected category lands it under Digital; fail loudly (v4 §A).
    if readback is None or not resumed:
        readback = await client.get_listing(listing_id, **ctx)
    stored_taxonomy = readback.get("taxonomy_id")
    if int(stored_taxonomy or 0) != int(taxonomy_id):
        raise ValueError(
            f"draft taxonomy_id {stored_taxonomy} does not match reference {taxonomy_id}; "
            "the listing would be in the wrong category"
        )
    # Same check for "How does your shop produce this item?" (v5 §D): who made it,
    # when, and with which production partners, exactly as on the reference.
    differs = [
        field
        for field in ("who_made", "when_made")
        if readback.get(field) != listing.get(field)
    ]
    if production_partner_ids(readback) != sorted(listing.get("production_partner_ids") or []):
        differs.append("production partners")
    if differs:
        raise ValueError(
            "the draft's production details do not match the reference ("
            + ", ".join(differs)
            + "); check them in Shop Manager"
        )

    # 5a2) Personalization, as the profile says (v7 §D4), checked on read-back.
    if personalization and personalization.get("enabled"):
        await client.update_listing_personalization(
            shop_id, listing_id, questions=questions_for(personalization), **ctx
        )
        back = await client.get_listing_personalization(listing_id, **ctx)
        texts = [q.get("question_text") for q in back.get("personalization_questions") or []]
        if personalization["question_text"] not in texts:
            raise ValueError(
                "the draft's personalization question did not save; check it in Shop Manager"
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
    # Images go up one at a time in order, so "how many the listing has" says
    # exactly which are done. A resumed draft starts from that count; an upload
    # whose answer never came is checked the same way before it is sent again.
    ordered = _rank([thumbnail, *extras])

    async def have() -> int:
        return len((await client.get_listing_images(listing_id, **ctx)).get("results") or [])

    async def put(position: int, send: Any) -> None:
        for tries in range(1, UPLOAD_TRIES + 1):
            try:
                await send()
                return
            except UNCERTAIN:
                if await have() > position:  # it arrived; only the answer was lost
                    return
                if tries == UPLOAD_TRIES:
                    raise

    done = await have() if resumed else 0
    for position, image in enumerate(ordered):
        if position < done:
            continue
        await put(position, lambda image=image: client.upload_listing_image(
            shop_id,
            listing_id,
            image_bytes=image.data,
            filename=image.filename,
            rank=image.rank,
            mime_type=image.mime_type,
            **ctx,
        ))
    next_rank = len(ordered)
    for image_id in fixed:
        next_rank += 1
        if next_rank - 1 < done:
            continue
        await put(next_rank - 1, lambda image_id=image_id, rank=next_rank: client.upload_listing_image(
            shop_id, listing_id, listing_image_id=image_id, rank=rank, **ctx
        ))

    # 8) Record the listing id. It is created as a DRAFT (state never set), so mark
    # it as such -- the UI links a draft to Shop Manager, not the public URL (A4).
    session.add(
        ListingPublication(
            tenant_id=tenant_id,
            content_id=content.id,
            connection_id=connection.id,
            profile_id=profile_id,
            etsy_listing_id=listing_id,
            state="draft",
            title=listing["title"],
            sku=sku,
            manual_done={},  # a new draft: nothing has been set by hand on it yet
        )
    )
    # One unit of the seller's product allowance (core/allowance.py).
    allowance.record(session, tenant_id, allowance.DRAFT)
    # Finished: from here the publication is the record, in the same commit.
    await session.delete(attempt)
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
    confirm_attempts: int = 3,
    confirm_wait: float = 5.0,
) -> PublishResult:
    """Make an existing DRAFT listing ACTIVE (the explicit "Publish now" step, E).

    Etsy can take longer to answer an activation than to apply it (docs/
    duzeltmeler-v6.md §A1: requests timed out after 30 s while the listings went
    live). So an activation that errors without a clear refusal is checked by
    reading the listing back: live means published.

    Publishing is never automatic: this runs only from the seller's explicit
    confirmation on a draft they reviewed and approved, either "Publish now" or a
    time they scheduled for it (CLAUDE.md rule 3, v6 §G). A listing that is no
    longer approved, or has a blocking compliance finding, is not published.
    """
    tenant_id = connection.tenant_id
    ctx = {"access_token": access_token, "tenant_id": tenant_id, "tenant_limit": tenant_limit}

    if not content.approved:
        raise PublishBlocked("the listing is no longer approved")
    if await _has_blocking_finding(session, content.id):
        raise PublishBlocked("content has a blocking compliance finding")
    publication = await publication_for(session, content.id, connection.id)
    if publication is None:
        raise ValueError("no draft listing in this shop to publish; create the draft first")

    # updateListing is shop-scoped, so resolve (and cache) the shop id.
    if connection.shop_id is None:
        if connection.etsy_user_id is None:
            raise ValueError("connection has no Etsy user id")
        shop = _first_shop(await client.get_shop_by_owner_user_id(connection.etsy_user_id, **ctx))
        connection.shop_id = int(shop["shop_id"])
        await session.commit()
    shop_id = connection.shop_id
    listing_id = publication.etsy_listing_id

    # Snapshot the pre-change state so the go-live can be rolled back to draft.
    session.add(
        ListingSnapshot(
            tenant_id=tenant_id,
            listing_id=listing_id,
            job_id=job_id,
            payload={"operation": "publish_live", "previous_state": publication.state},
        )
    )
    await session.commit()

    try:
        await client.update_listing(shop_id, listing_id, updates={"state": "active"}, repeat=False, **ctx)
    except (httpx.TransportError, EtsyServerError) as exc:
        # No answer, or a server error: Etsy may still have applied it. Ask.
        if not await _became_active(
            client, listing_id, ctx, attempts=confirm_attempts, wait=confirm_wait
        ):
            raise ValueError(
                "Etsy did not confirm the listing went live; it is still a draft. "
                "Try Publish now again."
            ) from exc
        logger.info("publish-live: listing %s confirmed active after %s", listing_id, type(exc).__name__)
    except EtsyClientError as exc:
        if exc.status_code not in (404, 410):
            raise
        # Etsy has no such listing in this shop. If reading it says the same, the
        # draft was deleted in Shop Manager: forget it, so it can be created again.
        try:
            await client.get_listing(listing_id, **ctx)
        except EtsyClientError as gone:
            if gone.status_code not in (404, 410):
                raise
            await session.delete(publication)
            await session.commit()
            raise ValueError(
                "This draft is no longer on Etsy (it was deleted in Shop Manager), so there was nothing "
                "to publish. Create the draft again, then publish it."
            ) from exc
        raise
    publication.state = "active"
    publication.published_at = _now()
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
