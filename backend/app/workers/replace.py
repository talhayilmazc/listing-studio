"""Worker job: replace an existing listing's artwork images in place (B4).

Uploads a folder of new product photos to an existing listing, deletes only its
artwork images (size charts are kept and re-ranked after the new photos), and
refreshes the title / 13 tags / description-title-block — leaving category, price,
variations, shipping, partners, section and state untouched. Snapshots before any
write. Queue-only, own-shop data only, tokens never logged.

Two ways (``payload["mode"]``): **photos** changes only the images, calls no AI
and so is not a listing generated; **full** also analyses the new cover and
writes a new title and tags (one listing generated). Both count their Etsy
requests against the account's ceiling.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.compliance.scanner import rescan
from app.compliance.trademarks import blocklist_for_tenant
from app.core import ai_meter, allowance, limits, llm_status
from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.llm_status import LLMUnavailable
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingProfile,
    ListingPublication,
    Tenant,
)
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService
from app.etsy.publisher import PublishImage, replace_listing_images
from app.pipeline import chart_order, versions
from app.pipeline.content import (
    AnthropicContentGenerator,
    content_template_for,
    policy_for,
    search_style,
    uses_search_style,
)
from app.pipeline.imageclass import (
    SIZE_CHART,
    AnthropicImageKindClassifier,
    classify_reference_images,
)
from app.pipeline.images import cover_image
from app.pipeline.llm import client_for
from app.pipeline.reference import decode_etsy_text, replace_title_block, with_opening
from app.pipeline.storage import LocalStorage
from app.pipeline.taxonomy import clothing_taxonomy_ids, infer_content_template
from app.pipeline.templates import load_template
from app.pipeline.vision import AnthropicVisionAnalyzer
from app.workers.gate import start_job
from app.workers.guards import owned, public_error

logger = logging.getLogger(__name__)


async def _chart_slots(session: Any, job: Job, batch_id: uuid.UUID, charts: int) -> list[int]:
    """Where the kept size charts go among the new photos: the group's own drag,
    else its size-chart profile's position, else the listing's profile's, else last."""
    from app.db.models import ListingGroupSetting, ListingProfile, UploadBatch

    key = job.payload.get("group_key")
    setting = None
    if key is not None:
        setting = (await session.execute(
            select(ListingGroupSetting).where(
                ListingGroupSetting.batch_id == batch_id, ListingGroupSetting.group_key == (key or "")
            )
        )).scalar_one_or_none()
    profile_id = setting.size_chart_profile_id if setting is not None else None
    if profile_id is None:
        batch = await session.get(UploadBatch, batch_id)
        profile_id = batch.size_chart_profile_id if batch is not None and batch.tenant_id == job.tenant_id else None
    if profile_id is None and job.payload.get("content_id"):
        content = await session.get(GeneratedContent, uuid.UUID(job.payload["content_id"]))
        profile_id = content.listing_profile_id if content is not None and content.tenant_id == job.tenant_id else None
    profile = await session.get(ListingProfile, profile_id) if profile_id is not None else None
    position = profile.size_chart_position if profile is not None and profile.tenant_id == job.tenant_id else None
    return chart_order.clean_slots(setting.chart_slots if setting is not None else None, charts, position)


async def run_replace_images_job(ctx: dict[str, Any], job_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    storage = LocalStorage(settings.storage_dir)
    service = ConnectionService(
        get_cipher(), client_id=settings.etsy_client_id, token_url=settings.etsy_oauth_token_url
    )

    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            return "missing"
        # Suspended tenant -> cancelled; no budget left -> paused until the reset.
        if (early := await start_job(ctx, session, job, "run_replace_images_job")) is not None:
            return early

        # The listing's text is rewritten before any image on Etsy is touched. While
        # our AI provider is refusing us, wait: do not read Etsy, do not fail.
        # "photos": only the images change, with no AI call. "full" (and a job
        # queued before the choice existed) also writes a new title and tags.
        full = job.payload.get("mode", "full") != "photos"
        if full and (await llm_status.current(ctx.get("redis")) if ctx.get("redis") is not None else False):
            return await _wait_for_llm(ctx, session, job)
        try:
            if full and not settings.llm_api_key:
                raise ValueError("writing the new listing is not available right now; try again later")
            listing_id = int(job.payload["listing_id"])
            batch_id = uuid.UUID(job.payload["batch_id"])
            connection = owned(
                await session.get(EtsyConnection, job.connection_id),
                job.tenant_id,
                "connection",
            )
            tenant = await session.get(Tenant, job.tenant_id)
            if tenant is None:
                raise ValueError("replace job missing tenant")

            token = await service.get_valid_access_token(session, connection)

            # New photos for this listing. One listing group of a batch comes in
            # the order the seller set, cover first (v6 §E); a whole batch (B4)
            # alphabetically. Primary = first.
            query = select(Asset).where(
                Asset.batch_id == batch_id,
                Asset.tenant_id == job.tenant_id,  # B5
                Asset.status == AssetStatus.processed,
            )
            if "group_key" in job.payload:
                key = job.payload["group_key"] or None
                query = query.where(
                    Asset.group_key == key if key is not None else Asset.group_key.is_(None)
                ).order_by(Asset.rank, Asset.original_filename)
            else:
                query = query.order_by(Asset.original_filename)
            rows = await session.execute(query)
            assets = [a for a in rows.scalars() if a.processed_key is not None]
            if not assets:
                raise ValueError("no processed images uploaded for the replacement")
            primary = assets[0]
            primary_bytes = storage.get(primary.processed_key)

            # The cover's crop, when the seller set one (as on draft creation).
            thumb = cover_image(
                primary_bytes,
                primary.cover_crop,
                padding_pct=settings.thumbnail_padding_pct,
                size=settings.thumbnail_size,
                mode=settings.thumbnail_mode,
            )
            new_images = [PublishImage(data=thumb.data, filename=f"{primary.id}-thumb.jpg")]
            for extra in assets[1:]:
                new_images.append(
                    PublishImage(
                        data=storage.get(extra.processed_key),
                        filename=f"{extra.id}.jpg",
                        mime_type=extra.mime_type or "image/jpeg",
                    )
                )

            llm = client_for(settings, "content") if full else None
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = EtsyApiClient(
                    client_id=settings.etsy_client_id,
                    shared_secret=settings.etsy_client_secret,
                    http_client=http,
                    bucket=ctx["bucket"],
                    quota=ctx["quota"],
                    usage=ctx.get("usage"),
                    cache=ctx.get("redis"),
                    shop=connection.id,
                )
                kw = {
                    "access_token": token,
                    "tenant_id": tenant.id,
                    "tenant_limit": limits.ceiling_limit(tenant),
                }
                shop_id = await _resolve_shop_id(session, client, connection, kw)

                existing = await client.get_listing(listing_id, **kw)
                images_resp = await client.get_listing_images(listing_id, **kw)
                # The category tree only picks the writing template: not read for photos only.
                nodes = await client.get_seller_taxonomy_nodes(**kw) if full else {}

                # Classify the listing's OWN images: keep charts, delete artwork.
                images_meta = [
                    {
                        "listing_image_id": r.get("listing_image_id"),
                        "rank": r.get("rank"),
                        "url": r.get("url_fullxfull") or r.get("url_570xN"),
                    }
                    for r in images_resp.get("results", [])
                ]
                async with httpx.AsyncClient(timeout=20.0) as img_http:

                    async def _fetch(url: str) -> tuple[bytes, str] | None:
                        try:
                            resp = await img_http.get(url)
                            resp.raise_for_status()
                            ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                            return resp.content, ctype or "image/jpeg"
                        except Exception:  # noqa: BLE001
                            return None

                    async def _keep_unsure(data: bytes, media_type: str) -> str:
                        # Photos only asks no AI. An image that might be a size chart
                        # stays (after the new photos) rather than be deleted on a guess.
                        return SIZE_CHART

                    keep_ids = await classify_reference_images(
                        images_meta,
                        fetch_bytes=_fetch,
                        vision=AnthropicImageKindClassifier(llm).classify if full else _keep_unsure,
                        # The charts this app put on the draft are known by their ids.
                        prior_kinds={int(i): SIZE_CHART for i in job.payload.get("chart_ids") or []},
                    )
                # The charts in their order on the listing now.
                rank_of = {m["listing_image_id"]: m.get("rank") or 0 for m in images_meta}
                keep_ids = sorted(keep_ids, key=lambda i: rank_of.get(i, 0))
                delete_ids = [
                    m["listing_image_id"]
                    for m in images_meta
                    if m.get("listing_image_id") is not None
                    and m["listing_image_id"] not in keep_ids
                ]

                new_title = new_tags = new_description = None
                if full:
                    # Regenerate title + 13 tags from the new primary image. A draft this
                    # app made is written in its profile's title style ("Etsy recommended
                    # (short)" or "Long keyword"), exactly as a new listing would be;
                    # otherwise the long style, template from the listing's own taxonomy.
                    profile = await replace_profile(session, job, connection.id, listing_id)
                    if profile is not None and uses_search_style(profile):
                        template_name, style_args = content_template_for(profile), search_style(profile)
                        policy = policy_for(profile.content_template)
                        style = versions.SHORT
                    else:
                        template = (
                            profile.content_template if profile is not None else infer_content_template(
                                existing.get("taxonomy_id"),
                                clothing_taxonomy_ids(nodes),
                                default=settings.default_content_template,
                            )
                        )
                        template_name, style_args, policy = f"content/{template}", {}, policy_for(template)
                        style = versions.LONG
                    analyzer = AnthropicVisionAnalyzer(client_for(settings, "vision"))
                    generator = AnthropicContentGenerator(
                        llm,
                        template=load_template(template_name),
                        policy=policy,
                        title_prefix=str(job.payload.get("title_prefix") or ""),
                        trademarks=blocklist_for_tenant(tenant),
                        **style_args,
                    )
                    async with ai_meter.scope(tenant.id, session) as meter:
                        vision = await analyzer.analyze(primary_bytes, primary.mime_type or "image/jpeg")
                        result = await generator.generate(vision.analysis, primary.parsed_sku)
                        meter.listing_written()
                    allowance.record(session, tenant.id, allowance.GENERATION)
                    new_title = result.listing.title
                    new_tags = result.listing.tags
                    new_opening = result.listing.opening
                    if style == versions.SHORT:
                        # The new design-specific opening above the listing's own body.
                        new_description = with_opening(decode_etsy_text(existing.get("description")), new_opening)
                    else:
                        new_description = replace_title_block(
                            decode_etsy_text(existing.get("description")), new_title
                        )

                await replace_listing_images(
                    session,
                    job_id=job.id,
                    listing_id=listing_id,
                    shop_id=shop_id,
                    tenant_id=tenant.id,
                    client=client,
                    access_token=token,
                    tenant_limit=limits.ceiling_limit(tenant),
                    existing_listing=existing,
                    keep_image_ids=keep_ids,
                    delete_image_ids=delete_ids,
                    new_images=new_images,
                    chart_slots=await _chart_slots(session, job, batch_id, len(keep_ids)),
                    new_title=new_title,
                    new_tags=new_tags,
                    new_description=new_description,
                )
        except LLMUnavailable as exc:
            # Raised while writing, which is before the first change on Etsy.
            await session.rollback()
            if ctx.get("redis") is not None:
                await llm_status.report(ctx["redis"], exc)
            job = await session.get(Job, uuid.UUID(job_id), populate_existing=True)
            return await _wait_for_llm(ctx, session, job)
        except Exception as exc:  # noqa: BLE001 - record which step failed
            job.status = JobStatus.failed
            job.last_error = public_error(exc)
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            logger.exception("replace-images failed for job %s", job_id)
            return "failed"

        if full:
            # The listing's text changed on Etsy: a new version from now (Part D).
            publication = (await session.execute(
                select(ListingPublication).where(
                    ListingPublication.connection_id == connection.id,
                    ListingPublication.etsy_listing_id == listing_id,
                )
            )).scalars().first()
            if publication is not None:
                await versions.replaced(
                    session, publication, datetime.now(timezone.utc),
                    title=new_title, tags=new_tags, description=new_description, style=style,
                )
        # The review page shows what the listing now says on Etsy (photos only changed no text).
        if full and job.payload.get("content_id"):
            content = await session.get(GeneratedContent, uuid.UUID(job.payload["content_id"]))
            if content is not None and content.tenant_id == job.tenant_id:
                content.title, content.tags, content.description = new_title, new_tags, new_description
                content.written_from_asset_id = content.asset_id  # the cover just analysed
                # The style it is now written in (the review page and its versions read it).
                attrs = {k: v for k, v in (content.attributes or {}).items() if k != "search"}
                if style == versions.SHORT:
                    attrs["search"] = {"opening": new_opening}
                content.attributes = attrs
                await rescan(session, content)
        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"


async def replace_profile(
    session: Any, job: Job, connection_id: uuid.UUID, listing_id: int
) -> ListingProfile | None:
    """The profile a replaced listing is written with: the one the job names, else
    (a job queued before it named one) the profile of the draft this app made."""
    profile_id = job.payload.get("profile_id")
    if profile_id is None:
        made = (await session.execute(
            select(ListingPublication.profile_id).where(
                ListingPublication.tenant_id == job.tenant_id,
                ListingPublication.connection_id == connection_id,
                ListingPublication.etsy_listing_id == listing_id,
            )
        )).scalars().first()
        profile_id = str(made) if made else None
    if profile_id is None:
        return None
    profile = await session.get(ListingProfile, uuid.UUID(str(profile_id)))
    return profile if profile is not None and profile.tenant_id == job.tenant_id else None


async def _wait_for_llm(ctx: dict[str, Any], session: Any, job: Job) -> str:
    """Put the job back to run later; it has changed nothing on Etsy yet."""
    job.status = JobStatus.queued
    job.paused_reason = llm_status.WAIT_LLM
    job.scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=llm_status.JOB_WAIT_SECONDS)
    await session.commit()
    pool = ctx.get("redis")
    if pool is not None and hasattr(pool, "enqueue_job"):
        await pool.enqueue_job(
            "run_replace_images_job", str(job.id), _defer_by=timedelta(seconds=llm_status.JOB_WAIT_SECONDS)
        )
    return "deferred"


async def _resolve_shop_id(session, client, connection, kw) -> int:  # noqa: ANN001
    if connection.shop_id is not None:
        return connection.shop_id
    if connection.etsy_user_id is None:
        raise ValueError("connection has no Etsy user id")
    resp = await client.get_shop_by_owner_user_id(connection.etsy_user_id, **kw)
    shop = resp["results"][0] if resp.get("results") else resp
    connection.shop_id = int(shop["shop_id"])
    await session.commit()
    return connection.shop_id
