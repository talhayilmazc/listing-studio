"""Worker job: publish an approved content as an Etsy DRAFT listing.

Entered only via the queue. Prepares the thumbnail, groups any same-SKU images,
resolves a valid access token, and runs :func:`publish_content`. Job status and a
safe failure reason (which step failed) are written back; tokens are never logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.crypto import get_cipher
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    Tenant,
)
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService
from app.etsy.publisher import PublishConfig, PublishImage, publish_content
from app.pipeline.images import prepare_thumbnail
from app.pipeline.sizes import load_size_config
from app.pipeline.storage import LocalStorage

logger = logging.getLogger(__name__)


def publish_config(settings: Settings) -> PublishConfig:
    return PublishConfig(
        default_taxonomy_id=settings.etsy_default_taxonomy_id,
        price=settings.default_price,
        quantity=settings.default_quantity,
        currency=settings.etsy_currency,
        section_title=settings.etsy_default_section_title,
        who_made=settings.etsy_who_made,
        when_made=settings.etsy_when_made,
        listing_type=settings.etsy_listing_type,
    )


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
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        try:
            content = await session.get(GeneratedContent, uuid.UUID(job.payload["content_id"]))
            connection = await session.get(EtsyConnection, job.connection_id)
            asset = await session.get(Asset, content.asset_id) if content else None
            tenant = await session.get(Tenant, job.tenant_id)
            if not (content and connection and asset and tenant):
                raise ValueError("publish job is missing content/connection/asset/tenant")

            access_token = await connection_service.get_valid_access_token(session, connection)

            # Primary image -> prepared thumbnail (rank=1).
            primary_bytes = storage.get(asset.processed_key or asset.storage_key)
            thumb = prepare_thumbnail(
                primary_bytes,
                padding_pct=settings.thumbnail_padding_pct,
                size=settings.thumbnail_size,
            )
            thumbnail = PublishImage(data=thumb.data, filename=f"{asset.id}-thumb.jpg")

            # Same-SKU siblings -> extra images (original ratio), in rank order.
            extras: list[PublishImage] = []
            if asset.parsed_sku:
                rows = await session.execute(
                    select(Asset)
                    .where(
                        Asset.batch_id == asset.batch_id,
                        Asset.parsed_sku == asset.parsed_sku,
                        Asset.status == AssetStatus.processed,
                        Asset.id != asset.id,
                    )
                    .order_by(Asset.rank)
                )
                for sibling in rows.scalars():
                    if sibling.processed_key is None:
                        continue
                    extras.append(
                        PublishImage(
                            data=storage.get(sibling.processed_key),
                            filename=f"{sibling.id}.jpg",
                            mime_type=sibling.mime_type or "image/jpeg",
                        )
                    )

            # Optional size-chart image.
            size_chart = None
            if settings.size_chart_image and Path(settings.size_chart_image).is_file():
                size_chart = PublishImage(
                    data=Path(settings.size_chart_image).read_bytes(),
                    filename="size-chart.jpg",
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
                )
                await publish_content(
                    session,
                    job_id=job.id,
                    content=content,
                    connection=connection,
                    sku=asset.parsed_sku,
                    thumbnail=thumbnail,
                    extra_images=extras,
                    size_chart=size_chart,
                    client=client,
                    access_token=access_token,
                    config=publish_config(settings),
                    size_config=load_size_config(),
                    theme=str(vision.get("theme", "")),
                    occasion=str(vision.get("occasion", "")),
                    auto_create_sections=settings.auto_create_sections,
                    tenant_limit=tenant.daily_quota,
                )
        except Exception as exc:  # noqa: BLE001 - record which step failed
            job.status = JobStatus.failed
            job.last_error = f"{type(exc).__name__}: {exc}"[:500]
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            logger.exception("publish failed for job %s", job_id)
            return "failed"

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"
