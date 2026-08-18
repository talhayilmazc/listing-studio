"""Orchestrate vision -> content -> persistence for one asset.

Runs the design analysis and content generation, then persists a
``generated_content`` row (``approved=False``) with cost instrumentation. If the
content fails validation after its one retry, the asset is marked failed and no
row is written. ``taxonomy_id`` stays null until the Etsy client (step 4) lands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asset, AssetStatus, GeneratedContent
from app.pipeline.content import ContentGenerator, ContentValidationError
from app.pipeline.llm import Usage
from app.pipeline.vision import VisionAnalysis, VisionAnalyzer


@dataclass
class GenerationOutcome:
    status: str  # "generated" | "failed"
    analysis: VisionAnalysis
    usages: list[Usage]  # vision + every content attempt (for cost aggregation)
    generated_content_id: uuid.UUID | None = None
    errors: list[str] = field(default_factory=list)


async def generate_listing_content(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    asset_id: uuid.UUID,
    image_data: bytes,
    media_type: str,
    sku: str | None,
    analyzer: VisionAnalyzer,
    generator: ContentGenerator,
) -> GenerationOutcome:
    """Analyze the processed derivative, generate the listing, and persist it."""
    vision = await analyzer.analyze(image_data, media_type)

    try:
        result = await generator.generate(vision.analysis, sku)
    except ContentValidationError as exc:
        asset = await session.get(Asset, asset_id)
        if asset is not None:
            asset.status = AssetStatus.failed
        await session.commit()
        return GenerationOutcome(
            status="failed",
            analysis=vision.analysis,
            usages=[vision.usage, *exc.usages],
            errors=exc.errors,
        )

    listing = result.listing
    # Sum content attempts so the stored per-listing figure reflects retries too.
    input_tokens = sum(u.input_tokens for u in result.usages)
    output_tokens = sum(u.output_tokens for u in result.usages)

    content = GeneratedContent(
        tenant_id=tenant_id,
        batch_id=batch_id,
        asset_id=asset_id,
        title=listing.title,
        tags=listing.tags,
        description=listing.description,
        taxonomy_id=None,  # set by the Etsy taxonomy step (step 4)
        model_used=result.usages[-1].model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        approved=False,
    )
    session.add(content)
    await session.commit()
    await session.refresh(content)

    return GenerationOutcome(
        status="generated",
        analysis=vision.analysis,
        usages=[vision.usage, *result.usages],
        generated_content_id=content.id,
    )
