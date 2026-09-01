"""Worker job: replace an existing listing's artwork images in place (B4).

Uploads a folder of new product photos to an existing listing, deletes only its
artwork images (size charts are kept and re-ranked after the new photos), and
refreshes the title / 13 tags / description-title-block — leaving category, price,
variations, shipping, partners, section and state untouched. Snapshots before any
write. Queue-only, own-shop data only, tokens never logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.db.models import Asset, AssetStatus, EtsyConnection, Job, JobStatus, Tenant
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService
from app.etsy.publisher import PublishImage, replace_listing_images
from app.pipeline.content import AnthropicContentGenerator, policy_for
from app.pipeline.imageclass import AnthropicImageKindClassifier, classify_reference_images
from app.pipeline.images import prepare_thumbnail
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.reference import decode_etsy_text, replace_title_block
from app.pipeline.storage import LocalStorage
from app.pipeline.taxonomy import clothing_taxonomy_ids, infer_content_template
from app.pipeline.templates import load_template
from app.pipeline.vision import AnthropicVisionAnalyzer

logger = logging.getLogger(__name__)


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
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        try:
            if not settings.llm_api_key:
                raise ValueError("LLM_API_KEY not configured; cannot regenerate content")
            listing_id = int(job.payload["listing_id"])
            batch_id = uuid.UUID(job.payload["batch_id"])
            connection = await session.get(EtsyConnection, job.connection_id)
            tenant = await session.get(Tenant, job.tenant_id)
            if not (connection and tenant):
                raise ValueError("replace job missing connection/tenant")

            token = await service.get_valid_access_token(session, connection)

            # New photos for this listing, alphabetical (D1). Primary = first.
            rows = await session.execute(
                select(Asset)
                .where(Asset.batch_id == batch_id, Asset.status == AssetStatus.processed)
                .order_by(Asset.original_filename)
            )
            assets = [a for a in rows.scalars() if a.processed_key is not None]
            if not assets:
                raise ValueError("no processed images uploaded for the replacement")
            primary = assets[0]
            primary_bytes = storage.get(primary.processed_key)

            thumb = prepare_thumbnail(
                primary_bytes,
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

            llm = AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model)
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
                kw = {
                    "access_token": token,
                    "tenant_id": tenant.id,
                    "tenant_limit": tenant.daily_quota,
                }
                shop_id = await _resolve_shop_id(session, client, connection, kw)

                existing = await client.get_listing(listing_id, **kw)
                images_resp = await client.get_listing_images(listing_id, **kw)
                nodes = await client.get_seller_taxonomy_nodes(**kw)

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

                    keep_ids = await classify_reference_images(
                        images_meta,
                        fetch_bytes=_fetch,
                        vision=AnthropicImageKindClassifier(llm).classify,
                    )
                delete_ids = [
                    m["listing_image_id"]
                    for m in images_meta
                    if m.get("listing_image_id") is not None
                    and m["listing_image_id"] not in keep_ids
                ]

                # Regenerate title + 13 tags from the new primary image; template from
                # the listing's own taxonomy (Clothing -> apparel).
                template = infer_content_template(
                    existing.get("taxonomy_id"),
                    clothing_taxonomy_ids(nodes),
                    default=settings.default_content_template,
                )
                analyzer = AnthropicVisionAnalyzer(llm)
                generator = AnthropicContentGenerator(
                    llm, template=load_template(f"content/{template}"), policy=policy_for(template)
                )
                vision = await analyzer.analyze(primary_bytes, primary.mime_type or "image/jpeg")
                result = await generator.generate(vision.analysis, primary.parsed_sku)
                new_title = result.listing.title
                new_tags = result.listing.tags
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
                    tenant_limit=tenant.daily_quota,
                    existing_listing=existing,
                    keep_image_ids=keep_ids,
                    delete_image_ids=delete_ids,
                    new_images=new_images,
                    new_title=new_title,
                    new_tags=new_tags,
                    new_description=new_description,
                )
        except Exception as exc:  # noqa: BLE001 - record which step failed
            job.status = JobStatus.failed
            job.last_error = f"{type(exc).__name__}: {exc}"[:500]
            job.finished_at = datetime.now(timezone.utc)
            await session.commit()
            logger.exception("replace-images failed for job %s", job_id)
            return "failed"

        job.status = JobStatus.succeeded
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return "succeeded"


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
