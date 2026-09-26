"""Worker job: publish an approved content as an Etsy DRAFT listing.

Entered only via the queue. Prepares the thumbnail, groups any same-SKU images,
resolves a valid access token, and runs :func:`publish_content`. Job status and a
safe failure reason (which step failed) are written back; tokens are never logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.pipeline.targets import resolve_target
from app.workers.gate import start_job
from app.workers.guards import owned, owned_optional, public_error
from app.etsy.connection import ConnectionService
from app.pipeline.personalization import effective as effective_personalization
from app.etsy.publisher import PublishConfig, PublishImage, publish_content, publish_live
from app.pipeline.images import cover_image
from app.pipeline.storage import LocalStorage

logger = logging.getLogger(__name__)


def publish_config(settings: Settings) -> PublishConfig:
    return PublishConfig(quantity=settings.default_quantity)



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

async def run_publish_job(ctx: dict[str, Any], job_id: str) -> str:
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
            target = await resolve_target(
                session,
                content,
                connection,
                profile_id=uuid.UUID(chosen) if chosen else None,
            )
            if not target.ok:
                raise ValueError(target.reason or "no profile for this shop")
            profile = owned(target.profile, job.tenant_id, "profile")
            if profile.connection_id != connection.id:
                raise ValueError("the profile belongs to another shop")

            # Size charts (fixed images) may come from a different profile chosen per
            # group (v4 §E), then per batch (Task 4), else this shop's own profile.
            # Image ids belong to one shop, so an override only counts in its own shop.
            fixed_image_ids = profile.fixed_image_ids or []
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
                )
                if chart_profile is not None and chart_profile.connection_id == connection.id:
                    fixed_image_ids = chart_profile.fixed_image_ids or []

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
                await publish_content(
                    session,
                    job_id=job.id,
                    content=content,
                    connection=connection,
                    sku=asset.parsed_sku,
                    thumbnail=thumbnail,
                    extra_images=extras,
                    fixed_image_ids=fixed_image_ids,
                    client=client,
                    access_token=access_token,
                    config=publish_config(settings),
                    reference=profile.cached_payload,
                    personalization=effective_personalization(profile.personalization, profile.cached_payload),
                    theme=str(vision.get("theme", "")),
                    occasion=str(vision.get("occasion", "")),
                    vision=vision,
                    profile_name=profile.name,
                    auto_create_sections=settings.auto_create_sections,
                    tenant_limit=tenant.daily_quota,
                    profile_id=profile.id,
                    title=target.title,
                    description=target.description,
                )
        except Exception as exc:  # noqa: BLE001 - record which step failed
            job.status = JobStatus.failed
            job.last_error = public_error(exc)
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            logger.exception("publish failed for job %s", job_id)
            return "failed"

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"


async def run_publish_live_job(ctx: dict[str, Any], job_id: str) -> str:
    """Make an approved, already-created DRAFT listing ACTIVE (E "Publish now")."""
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
                    tenant_limit=tenant.daily_quota,
                )
        except Exception as exc:  # noqa: BLE001 - record which step failed
            job.status = JobStatus.failed
            job.last_error = public_error(exc)
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            logger.exception("publish-live failed for job %s", job_id)
            return "failed"

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"
