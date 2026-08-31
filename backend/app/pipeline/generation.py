"""Orchestrate vision -> content -> persistence for one asset.

Runs the design analysis and content generation, then persists a
``generated_content`` row (``approved=False``) with cost instrumentation.

Failures are never swallowed: the full traceback is logged, a safe reason is
stored on ``asset.error`` and returned in the outcome so the UI can show why.
The asset's image ``status`` is left as ``processed`` (the image is fine), so a
later retry picks it up again. ``taxonomy_id`` stays null until the Etsy client
(step 4) lands.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asset, GeneratedContent, ListingProfile
from app.pipeline.content import ContentGenerator, ContentValidationError
from app.pipeline.llm import Usage
from app.pipeline.reference import replace_title_block
from app.pipeline.vision import VisionAnalysis, VisionAnalyzer

logger = logging.getLogger(__name__)

_MAX_REASON = 500


@dataclass
class GenerationOutcome:
    status: str  # "generated" | "failed"
    usages: list[Usage]  # vision + every content attempt (for cost aggregation)
    analysis: VisionAnalysis | None = None
    generated_content_id: uuid.UUID | None = None
    error: str | None = None


async def _record_failure(session: AsyncSession, asset_id: uuid.UUID, reason: str) -> None:
    reason = reason[:_MAX_REASON]
    asset = await session.get(Asset, asset_id)
    if asset is not None:
        asset.error = reason
    await session.commit()


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
    profile: ListingProfile,
) -> GenerationOutcome:
    """Analyze the processed derivative, generate the listing, and persist it.

    A reference-listing ``profile`` is required (Section B): its ``cached_payload``
    supplies the description body (only the leading line(s) are replaced by the new
    title) and the taxonomy; the LLM only writes the title and 13 tags. The vision
    hints are stored on ``attributes`` for section matching.
    """
    usages: list[Usage] = []
    analysis: VisionAnalysis | None = None
    try:
        vision = await analyzer.analyze(image_data, media_type)
        usages.append(vision.usage)
        analysis = vision.analysis
        result = await generator.generate(vision.analysis, sku)
        usages.extend(result.usages)
    except ContentValidationError as exc:
        usages.extend(exc.usages)
        reason = "; ".join(exc.errors)
        logger.warning("content validation failed for asset %s: %s", asset_id, reason)
        await _record_failure(session, asset_id, reason)
        return GenerationOutcome(
            status="failed", usages=usages, analysis=analysis, error=reason
        )
    except Exception as exc:  # vision or provider error — log the real traceback
        reason = f"{type(exc).__name__}: {exc}"
        logger.exception("content generation failed for asset %s", asset_id)
        await _record_failure(session, asset_id, reason)
        return GenerationOutcome(
            status="failed", usages=usages, analysis=analysis, error=reason[:_MAX_REASON]
        )

    listing = result.listing
    # Sum content attempts so the stored per-listing figure reflects retries too.
    input_tokens = sum(u.input_tokens for u in result.usages)
    output_tokens = sum(u.output_tokens for u in result.usages)

    # Description comes from the reference listing; the title block (everything
    # before the first blank line) is replaced by the newly generated title (B2).
    payload = profile.cached_payload or {}
    description = replace_title_block(str(payload.get("description", "")), listing.title)
    taxonomy_id = payload.get("taxonomy_id")

    attributes = None
    if analysis is not None:
        attributes = {
            "vision": {
                "theme": analysis.theme,
                "occasion": analysis.occasion,
                "audience": analysis.target_audience,
            }
        }

    content = GeneratedContent(
        tenant_id=tenant_id,
        batch_id=batch_id,
        asset_id=asset_id,
        title=listing.title,
        tags=listing.tags,
        description=description,
        taxonomy_id=taxonomy_id,  # from the reference listing (never re-selected)
        listing_profile_id=profile.id,
        attributes=attributes,
        model_used=result.usages[-1].model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        approved=False,
    )
    session.add(content)
    # Clear any prior failure reason now that generation succeeded.
    asset = await session.get(Asset, asset_id)
    if asset is not None:
        asset.error = None
    await session.commit()
    await session.refresh(content)

    return GenerationOutcome(
        status="generated",
        usages=usages,
        analysis=analysis,
        generated_content_id=content.id,
    )
