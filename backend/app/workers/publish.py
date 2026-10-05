"""Worker job: publish an approved content as an Etsy DRAFT listing.

Entered only via the queue. Prepares the thumbnail, groups any same-SKU images,
resolves a valid access token, and runs :func:`publish_content`. Job status and a
safe failure reason (which step failed) are written back; tokens are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import limits
from app.core.config import Settings, get_settings
from app.core.crypto import get_cipher
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingGroupSetting,
    ListingProfile,
    Tenant,
    UploadBatch,
)
from app.etsy.api import EtsyApiClient
from app.etsy.api import RateLimitExceeded
from app.pipeline.links import shop_reference
from app.pipeline.targets import Target, is_fresh, resolve_target
from app.workers import recovery
from app.workers.gate import start_job
from app.workers.guards import owned, owned_optional
from app.etsy.connection import ConnectionService
from app.pipeline.personalization import effective as effective_personalization
from app.etsy.publisher import PublishConfig, PublishImage, publish_content, publish_live
from app.pipeline.images import cover_image
from app.pipeline.storage import LocalStorage

logger = logging.getLogger(__name__)


def publish_config(settings: Settings) -> PublishConfig:
    return PublishConfig(quantity=settings.default_quantity)



class SizeChartsUnavailable(ValueError):
    """The main shop's size charts could not be copied for another shop's draft."""


def _links_fresh(profile: ListingProfile) -> bool:
    stamp = profile.images_updated_at or profile.updated_at
    if stamp is None:
        return False
    if stamp.tzinfo is None:  # SQLite hands back naive datetimes
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age < ListingProfile.DISPLAY_MAX_AGE_SECONDS


async def copied_size_charts(
    ctx: dict[str, Any], session: AsyncSession, profile: ListingProfile, fetch: Any = None
) -> list[PublishImage]:
    """The profile's size charts as images to upload to another shop's draft.

    Etsy accepts an image id only in its own shop, so for any other shop the
    charts are fetched from the main shop's image links (renewed first if past
    their 6-hour limit), held in memory for the upload and never stored.
    """
    wanted = list(profile.fixed_image_ids or [])
    if not wanted:
        return []

    def links() -> dict[int, str]:
        return {
            int(img["listing_image_id"]): str(img.get("url") or img.get("display_url"))
            for img in (profile.cached_payload or {}).get("images", [])
            if img.get("listing_image_id") is not None and (img.get("url") or img.get("display_url"))
        }

    if not _links_fresh(profile) or not set(wanted) <= set(links()):
        from app.workers.profiles import refresh_profile_images  # late: profiles imports gate

        await refresh_profile_images(ctx, str(profile.id))
        await session.refresh(profile)
    urls = links()
    if not set(wanted) <= set(urls):
        raise SizeChartsUnavailable(
            f'the size charts of profile "{profile.name}" could not be read from its main shop; '
            "refresh the profile, then try again"
        )

    images: list[PublishImage] = []
    async with httpx.AsyncClient(timeout=30.0) as img_http:  # no Etsy headers to the image host
        for image_id in wanted:
            try:
                if fetch is not None:
                    data, mime = await fetch(urls[image_id])
                else:
                    resp = await img_http.get(urls[image_id])
                    resp.raise_for_status()
                    data = resp.content
                    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0] or "image/jpeg"
            except Exception as exc:  # noqa: BLE001 - one message for every way a fetch fails
                raise SizeChartsUnavailable(
                    "a size chart could not be copied from the profile's main shop; try again"
                ) from exc
            images.append(PublishImage(data=data, filename=f"size-chart-{image_id}.jpg", mime_type=mime))
    return images


async def group_siblings(session: AsyncSession, cover: Asset) -> list[Asset]:
    """The other processed images of ``cover``'s listing group, in the order the
    seller set (v6 §E). One folder = one listing (D1); the files at the root of
    an upload are one group too."""
    rows = await session.execute(
        select(Asset)
        .where(
            Asset.batch_id == cover.batch_id,
            Asset.group_key == cover.group_key
            if cover.group_key is not None
            else Asset.group_key.is_(None),
            Asset.status == AssetStatus.processed,
            Asset.id != cover.id,
            Asset.processed_key.is_not(None),
        )
        .order_by(Asset.rank, Asset.original_filename)
    )
    return list(rows.scalars())

# One refresh at a time per profile in this worker: fifty drafts from one stale
# profile wait for a single refresh instead of each asking Etsy for the same data.
_refreshing: dict[uuid.UUID, asyncio.Lock] = {}


async def _fresh_target(
    ctx: dict[str, Any], session: AsyncSession, content: GeneratedContent, connection: EtsyConnection,
    profile_id: uuid.UUID | None,
) -> Target:
    """The shop's profile for this draft, refreshed first if its Etsy data is stale.

    The API checked freshness when the job was queued, but a job can wait (a
    long batch, a pause until the daily reset) past the profile's 24 hours, and
    a deploy can make every stored payload out of date at once. That used to
    fail every listing of the batch with "refresh the profile, then try again";
    the job now does the refresh itself, once, and carries on.
    """
    target = await resolve_target(session, content, connection, profile_id=profile_id)
    if target.ok or not target.stale or target.profile is None:
        return target
    profile = target.profile
    from app.workers.profiles import refresh_profile  # late: profiles imports this package's gate

    lock = _refreshing.setdefault(profile.id, asyncio.Lock())
    async with lock:
        await session.refresh(profile)
        if not is_fresh(profile):
            result = await refresh_profile(ctx, str(profile.id))
            if result == "deferred":
                # No budget left today even for the refresh: wait for the reset.
                raise RateLimitExceeded("daily Etsy API budget exhausted")
            await session.refresh(profile)
    target = await resolve_target(session, content, connection, profile_id=profile_id)
    if target.stale and profile.refresh_error:
        return Target(connection, profile, profile.refresh_error, stale=True)
    return target


async def run_publish_job(ctx: dict[str, Any], job_id: str) -> str:
    async with recovery.holding(ctx, job_id) as held:
        if not held:
            return "busy"  # another copy of this job is running; it will finish the draft
        try:
            return await _run_publish_job(ctx, job_id)
        except asyncio.CancelledError:
            await recovery.interrupted(ctx, None, uuid.UUID(job_id), "run_publish_job")
            raise


async def _run_publish_job(ctx: dict[str, Any], job_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    storage = LocalStorage(settings.storage_dir)
    connection_service = ConnectionService(
        get_cipher(),
        client_id=settings.etsy_client_id,
        token_url=settings.etsy_oauth_token_url,
    )

    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            return "missing"
        # Suspended tenant -> cancelled; no budget left -> paused until the reset.
        if (early := await start_job(ctx, session, job, "run_publish_job")) is not None:
            return early

        try:
            # Every row this job touches must belong to the job's tenant (B5).
            content = owned(
                await session.get(GeneratedContent, uuid.UUID(job.payload["content_id"])),
                job.tenant_id,
                "content",
            )
            connection = owned(
                await session.get(EtsyConnection, job.connection_id),
                job.tenant_id,
                "connection",
            )
            asset = owned(await session.get(Asset, content.asset_id), job.tenant_id, "asset")
            tenant = await session.get(Tenant, job.tenant_id)
            if tenant is None:
                raise ValueError("publish job is missing its tenant")
            # This shop's own profile builds this shop's draft (v5 §E); resolved
            # again here so the text and the freshness check are current.
            chosen = job.payload.get("profile_id")
            target = await _fresh_target(
                ctx, session, content, connection, uuid.UUID(chosen) if chosen else None
            )
            if not target.ok:
                raise ValueError(target.reason or "no profile for this shop")
            profile = owned(target.profile, job.tenant_id, "profile")
            link = target.link
            if link is None or link.connection_id != connection.id:
                raise ValueError("the profile is not set up in this shop")
            # The profile's shared settings with this shop's own ids (v8 §C).
            reference = shop_reference(
                profile.cached_payload or {}, None if connection.id == profile.connection_id else link
            )

            # Size charts (fixed images) may come from a different profile chosen per
            # group (v4 §E), then per batch (Task 4), else the listing's own profile.
            chart_profile: ListingProfile | None = profile
            chart_profile_id = None
            group_setting = (
                await session.execute(
                    select(ListingGroupSetting).where(
                        ListingGroupSetting.batch_id == content.batch_id,
                        ListingGroupSetting.group_key == (asset.group_key or ""),
                    )
                )
            ).scalar_one_or_none()
            if group_setting is not None and group_setting.size_chart_profile_id is not None:
                chart_profile_id = group_setting.size_chart_profile_id
            else:
                batch = owned_optional(
                    await session.get(UploadBatch, content.batch_id), job.tenant_id
                )
                if batch is not None and batch.size_chart_profile_id is not None:
                    chart_profile_id = batch.size_chart_profile_id
            if chart_profile_id is not None:
                chart_profile = owned_optional(
                    await session.get(ListingProfile, chart_profile_id), job.tenant_id
                ) or profile
            # Image ids belong to one shop: in the charts' own shop they are re-used
            # by id; in any other shop they are copied (fetched in memory, uploaded,
            # never stored; v8 §C).
            fixed: list[int | PublishImage] = list(chart_profile.fixed_image_ids or [])
            if fixed and chart_profile.connection_id != connection.id:
                fixed = list(await copied_size_charts(ctx, session, chart_profile, ctx.get("image_fetch")))

            access_token = await connection_service.get_valid_access_token(session, connection)

            # Primary image -> prepared thumbnail (rank=1).
            primary_bytes = storage.get(asset.processed_key or asset.storage_key)
            # The seller's crop if they set one, else the automatic square.
            thumb = cover_image(
                primary_bytes,
                asset.cover_crop,
                padding_pct=settings.thumbnail_padding_pct,
                size=settings.thumbnail_size,
                mode=settings.thumbnail_mode,
            )
            thumbnail = PublishImage(data=thumb.data, filename=f"{asset.id}-thumb.jpg")

            # Same-folder-group siblings -> extra images (original ratio), in the
            # order the seller set (v6 §E). One folder = one listing (D1), and the
            # files at the root of an upload are one group too.
            extras: list[PublishImage] = []
            for sibling in await group_siblings(session, asset):
                extras.append(
                    PublishImage(
                        data=storage.get(sibling.processed_key),
                        filename=f"{sibling.id}.jpg",
                        mime_type=sibling.mime_type or "image/jpeg",
                    )
                )

            vision = (content.attributes or {}).get("vision", {})
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=90.0)) as http:
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
                await publish_content(
                    session,
                    job_id=job.id,
                    content=content,
                    connection=connection,
                    sku=asset.parsed_sku,
                    thumbnail=thumbnail,
                    extra_images=extras,
                    fixed_image_ids=fixed,
                    client=client,
                    access_token=access_token,
                    config=publish_config(settings),
                    reference=reference,
                    personalization=effective_personalization(profile.personalization, profile.cached_payload),
                    theme=str(vision.get("theme", "")),
                    occasion=str(vision.get("occasion", "")),
                    vision=vision,
                    optional_attributes=(content.attributes or {}).get("listing"),
                    profile_name=profile.name,
                    auto_create_sections=settings.auto_create_sections,
                    tenant_limit=limits.ceiling_limit(tenant),
                    profile_id=profile.id,
                    title=target.title,
                    description=target.description,
                )
        except Exception as exc:  # noqa: BLE001 - wait and run again, or record why it failed
            return await recovery.after_failure(ctx, session, uuid.UUID(job_id), exc, "run_publish_job")

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        if (job.payload or {}).get("planned_slot"):
            # A group schedule's draft: its go-live is set for the confirmed time (v8 §B).
            from app.workers.plans import after_draft

            await after_draft(session, content.id, connection.id)
        return "succeeded"


async def run_publish_live_job(ctx: dict[str, Any], job_id: str) -> str:
    """Make an approved, already-created DRAFT listing ACTIVE (E "Publish now")."""
    async with recovery.holding(ctx, job_id) as held:
        if not held:
            return "busy"
        try:
            return await _run_publish_live_job(ctx, job_id)
        except asyncio.CancelledError:
            await recovery.interrupted(ctx, None, uuid.UUID(job_id), "run_publish_live_job")
            raise


async def _run_publish_live_job(ctx: dict[str, Any], job_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    connection_service = ConnectionService(
        get_cipher(),
        client_id=settings.etsy_client_id,
        token_url=settings.etsy_oauth_token_url,
    )

    async with sessionmaker() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            return "missing"
        # Suspended tenant -> cancelled; no budget left -> paused until the reset.
        if (early := await start_job(ctx, session, job, "run_publish_live_job")) is not None:
            return early

        try:
            content = owned(
                await session.get(GeneratedContent, uuid.UUID(job.payload["content_id"])),
                job.tenant_id,
                "content",
            )
            connection = owned(
                await session.get(EtsyConnection, job.connection_id),
                job.tenant_id,
                "connection",
            )
            tenant = await session.get(Tenant, job.tenant_id)
            if not (content and connection and tenant):
                raise ValueError("publish-live job is missing content/connection/tenant")

            access_token = await connection_service.get_valid_access_token(session, connection)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=90.0)) as http:
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
                await publish_live(
                    session,
                    job_id=job.id,
                    content=content,
                    connection=connection,
                    client=client,
                    access_token=access_token,
                    tenant_limit=limits.ceiling_limit(tenant),
                )
        except Exception as exc:  # noqa: BLE001 - wait and run again, or record why it failed
            return await recovery.after_failure(ctx, session, uuid.UUID(job_id), exc, "run_publish_live_job")

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"
