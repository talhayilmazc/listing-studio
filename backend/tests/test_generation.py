"""End-to-end generation + persistence (provider mocked, real SQLite)."""

import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)

# Reference description: only the first line (the old title) is replaced (B2).
REF_DESCRIPTION = "Old Reference Title\nSize: S-3XL\nShips in 3 days.\nReturns accepted."
from app.pipeline.content import AnthropicContentGenerator
from app.pipeline.generation import generate_listing_content
from app.pipeline.vision import AnthropicVisionAnalyzer
from app.pipeline.llm import AnthropicLLMClient
from tests.support import VALID_TITLE, FakeMessages, fake_response

VISION_DATA = {
    "theme": "cozy autumn coffee",
    "embedded_text": "but first, coffee",
    "style": "hand-lettered",
    "colors": ["rust", "cream"],
    "target_audience": "coffee lovers",
    "product_type_hints": ["mug", "printable"],
}


def _content(tags: list[str]) -> dict:
    return {
        "title": VALID_TITLE,
        "tags": tags,
        "description": "A warm hand-lettered design. Instant digital download.",
    }


async def _seed(sm: async_sessionmaker) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    async with sm() as session:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        session.add(tenant)
        await session.flush()
        conn = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active)
        session.add(conn)
        await session.flush()
        batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=1)
        session.add(batch)
        profile = ListingProfile(
            tenant_id=tenant.id,
            connection_id=conn.id,
            name="Standard Tee",
            reference_listing_id=111,
            content_template="digital_products",
            cached_payload={"description": REF_DESCRIPTION, "taxonomy_id": 2078},
            updated_at=datetime.now(timezone.utc),
        )
        session.add(profile)
        await session.flush()
        asset = Asset(
            batch_id=batch.id,
            tenant_id=tenant.id,
            original_filename="SKU1_front.png",
            parsed_sku="SKU1",
            storage_key="k",
            status=AssetStatus.processed,
            rank=1,
        )
        session.add(asset)
        await session.commit()
        return tenant.id, batch.id, asset.id, profile.id


def _pipeline(messages: FakeMessages) -> tuple[AnthropicVisionAnalyzer, AnthropicContentGenerator]:
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)
    return AnthropicVisionAnalyzer(client), AnthropicContentGenerator(client)


async def test_success_persists_generated_content(
    async_sm: async_sessionmaker, make_image: Callable[..., bytes]
) -> None:
    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    tags = [f"tag{i}" for i in range(13)]
    messages = FakeMessages(
        [
            fake_response(VISION_DATA, input_tokens=1200, output_tokens=90),
            fake_response(_content(tags), input_tokens=300, output_tokens=120),
        ]
    )
    analyzer, generator = _pipeline(messages)

    async with async_sm() as session:
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant_id,
            batch_id=batch_id,
            asset_id=asset_id,
            image_data=make_image(400, 300),
            media_type="image/png",
            sku="SKU1",
            analyzer=analyzer,
            generator=generator,
            profile=await session.get(ListingProfile, profile_id),
        )

    assert outcome.status == "generated"
    assert outcome.generated_content_id is not None
    # vision + one content attempt
    assert len(outcome.usages) == 2

    async with async_sm() as session:
        row = await session.get(GeneratedContent, outcome.generated_content_id)
        assert row is not None
        assert row.approved is False
        # Taxonomy comes from the reference profile, never re-selected (A3/B).
        assert row.taxonomy_id == 2078
        assert row.listing_profile_id == profile_id
        # Description is the reference body with only the first line replaced (B2).
        assert row.description.split("\n")[0] == VALID_TITLE
        assert "Size: S-3XL" in row.description
        assert "Old Reference Title" not in row.description
        assert row.model_used == "claude-haiku-4-5-20251001"
        assert row.input_tokens == 300 and row.output_tokens == 120
        assert len(row.tags) == 13


async def test_retry_tokens_are_summed_into_row(
    async_sm: async_sessionmaker, make_image: Callable[..., bytes]
) -> None:
    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    good = [f"tag{i}" for i in range(13)]
    bad = [f"tag{i}" for i in range(12)]
    messages = FakeMessages(
        [
            fake_response(VISION_DATA, input_tokens=1000, output_tokens=80),
            fake_response(_content(bad), input_tokens=300, output_tokens=100),  # invalid
            fake_response(_content(good), input_tokens=320, output_tokens=110),  # valid
        ]
    )
    analyzer, generator = _pipeline(messages)

    async with async_sm() as session:
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant_id,
            batch_id=batch_id,
            asset_id=asset_id,
            image_data=make_image(200, 200),
            media_type="image/png",
            sku="SKU1",
            analyzer=analyzer,
            generator=generator,
            profile=await session.get(ListingProfile, profile_id),
        )

    async with async_sm() as session:
        row = await session.get(GeneratedContent, outcome.generated_content_id)
        # Both content attempts counted: 300+320 in, 100+110 out.
        assert row.input_tokens == 620
        assert row.output_tokens == 210


async def test_validation_failure_records_error_and_keeps_retryable(
    async_sm: async_sessionmaker, make_image: Callable[..., bytes]
) -> None:
    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    messages = FakeMessages(
        [
            fake_response(VISION_DATA),
            fake_response(_content([f"tag{i}" for i in range(11)])),  # invalid
            fake_response(_content([f"tag{i}" for i in range(10)])),  # invalid again
        ]
    )
    analyzer, generator = _pipeline(messages)

    async with async_sm() as session:
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant_id,
            batch_id=batch_id,
            asset_id=asset_id,
            image_data=make_image(200, 200),
            media_type="image/png",
            sku="SKU1",
            analyzer=analyzer,
            generator=generator,
            profile=await session.get(ListingProfile, profile_id),
        )

    assert outcome.status == "failed"
    assert outcome.generated_content_id is None
    assert outcome.error and "13 tags" in outcome.error

    async with async_sm() as session:
        asset = await session.get(Asset, asset_id)
        # Image is fine, so status stays processed (retryable); the reason is stored.
        assert asset.status is AssetStatus.processed
        assert asset.error and "13 tags" in asset.error
        rows = await session.execute(
            select(GeneratedContent).where(GeneratedContent.asset_id == asset_id)
        )
        assert rows.first() is None  # nothing persisted on failure


class _BoomAnalyzer:
    async def analyze(self, image_data, media_type):  # noqa: ANN001, ANN201
        raise RuntimeError("vision provider exploded")


async def test_unexpected_exception_is_logged_and_recorded(
    async_sm: async_sessionmaker, make_image: Callable[..., bytes], caplog
) -> None:
    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    _, generator = _pipeline(FakeMessages([]))

    with caplog.at_level("ERROR"):
        async with async_sm() as session:
            outcome = await generate_listing_content(
                session,
                tenant_id=tenant_id,
                batch_id=batch_id,
                asset_id=asset_id,
                image_data=make_image(120, 120),
                media_type="image/png",
                sku="SKU1",
                analyzer=_BoomAnalyzer(),
                generator=generator,
                profile=await session.get(ListingProfile, profile_id),
            )

    assert outcome.status == "failed"
    assert outcome.error == "RuntimeError: vision provider exploded"
    # The full traceback was logged (not swallowed).
    assert any(rec.exc_info for rec in caplog.records)

    async with async_sm() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.error == "RuntimeError: vision provider exploded"
        assert asset.status is AssetStatus.processed


async def test_prefixed_title_leads_the_description_and_counts_toward_the_length(
    async_sm: async_sessionmaker, make_image: Callable[..., bytes]
) -> None:
    """docs/duzeltmeler-v5.md §B: prefix stored on the profile -> title starts with it,
    110-140 characters including it, and the description's title block is replaced
    by the prefixed title."""
    from tests.test_content import APPAREL

    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    async with async_sm() as session:
        profile = await session.get(ListingProfile, profile_id)
        profile.title_prefix = "Comfort Colors®"
        profile.content_template = "apparel"
        await session.commit()

    remainder = (
        "Retro Frog Tee, Cottagecore Shirt, Vintage Frog Graphic Top, Nature Lover Gift, "
        "Pond Life Crewneck Tee"
    )
    tags = ["shirt", *[f"tag{i}" for i in range(12)]]
    messages = FakeMessages(
        [
            fake_response(VISION_DATA),
            fake_response({"title": remainder, "tags": tags, "description": "x"}),
        ]
    )
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)

    async with async_sm() as session:
        profile = await session.get(ListingProfile, profile_id)
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant_id,
            batch_id=batch_id,
            asset_id=asset_id,
            image_data=make_image(400, 300),
            media_type="image/png",
            sku="SKU1",
            analyzer=AnthropicVisionAnalyzer(client),
            generator=AnthropicContentGenerator(
                client, policy=APPAREL, title_prefix=profile.title_prefix
            ),
            profile=profile,
        )

    assert outcome.status == "generated", outcome
    async with async_sm() as session:
        row = await session.get(GeneratedContent, outcome.generated_content_id)
    assert row.title.startswith("Comfort Colors®, Retro Frog Tee")
    assert 110 <= len(row.title) <= 140
    assert row.description.split("\n")[0] == row.title
    assert "Size: S-3XL" in row.description
