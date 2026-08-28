"""publish_content tests with a fake Etsy client (no real API calls)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    Asset,
    AssetStatus,
    ComplianceFinding,
    ComplianceSeverity,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobType,
    ListingSnapshot,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.publisher import (
    PublishBlocked,
    PublishConfig,
    PublishImage,
    publish_content,
)
from app.pipeline.sizes import SizeConfig
from tests.support import VALID_TITLE

CONFIG = PublishConfig(
    default_taxonomy_id=1,
    price=5.0,
    quantity=999,
    currency="USD",
    section_title="Drafts",
    who_made="i_did",
    when_made="made_to_order",
    listing_type="download",
)


class FakeEtsy:
    def __init__(self, *, sections=None, properties=None) -> None:
        self.calls: list[str] = []
        self._sections = sections if sections is not None else {"results": []}
        self._properties = properties if properties is not None else {"results": []}
        self.last_listing: dict[str, Any] | None = None
        self.inventory: dict[str, Any] | None = None
        self.uploaded: list[tuple[int, str]] = []
        self.created_section_title: str | None = None

    async def get_shop_by_owner_user_id(self, uid: int, **_: Any) -> dict[str, Any]:
        self.calls.append("shop")
        return {"results": [{"shop_id": 900, "shop_name": "My Shop"}]}

    async def get_shop_sections(self, shop_id: int, **_: Any) -> dict[str, Any]:
        self.calls.append("sections")
        return self._sections

    async def create_shop_section(self, shop_id: int, *, title: str, **_: Any) -> dict[str, Any]:
        self.calls.append("create_section")
        self.created_section_title = title
        return {"shop_section_id": 77}

    async def get_properties_by_taxonomy_id(self, tid: int, **_: Any) -> dict[str, Any]:
        self.calls.append("props")
        return self._properties

    async def create_draft_listing(self, shop_id: int, *, listing: dict[str, Any], **_: Any):
        self.calls.append("create")
        self.last_listing = listing
        return {"listing_id": 555}

    async def update_listing_inventory(self, listing_id: int, *, inventory: dict[str, Any], **_: Any):
        self.calls.append("inventory")
        self.inventory = inventory
        return {}

    async def upload_listing_image(self, shop_id, listing_id, *, image_bytes, filename, rank, **_):
        self.uploaded.append((rank, filename))
        return {}


async def _seed(sm: async_sessionmaker, *, blocking: bool = False, taxonomy_id=None):
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        s.add(tenant)
        await s.flush()
        conn = EtsyConnection(
            tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900, shop_id=None
        )
        batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=1)
        s.add_all([conn, batch])
        await s.flush()
        asset = Asset(
            batch_id=batch.id,
            tenant_id=tenant.id,
            original_filename="tasarim_BR5475.png",
            parsed_sku="BR5475",
            storage_key="k",
            status=AssetStatus.processed,
            rank=1,
        )
        s.add(asset)
        await s.flush()
        content = GeneratedContent(
            tenant_id=tenant.id,
            batch_id=batch.id,
            asset_id=asset.id,
            title=VALID_TITLE,
            tags=[f"tag{i}" for i in range(13)],
            description="Fixed description.",
            taxonomy_id=taxonomy_id,
            approved=True,
        )
        job = Job(
            tenant_id=tenant.id,
            connection_id=conn.id,
            type=JobType.create_draft,
            payload={},
            batch_id=batch.id,
        )
        s.add_all([content, job])
        await s.flush()
        if blocking:
            s.add(
                ComplianceFinding(
                    tenant_id=tenant.id,
                    generated_content_id=content.id,
                    severity=ComplianceSeverity.blocking,
                    rule="trademark",
                )
            )
        await s.commit()
        return tenant.id, conn.id, content.id, job.id


async def test_publish_creates_draft_and_snapshot(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(
        async_sm, taxonomy_id=None
    )
    fake = FakeEtsy(
        sections={"results": [{"shop_section_id": 10, "title": "4th of July"}]},
        properties={"results": [{"name": "Size"}]},
    )
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        result = await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"thumb", "t.jpg"),
            extra_images=[PublishImage(b"extra", "e.jpg")],
            size_chart=PublishImage(b"chart", "c.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            size_config=SizeConfig("Size", ["S", "M"], {}),
            theme="patriotic eagle",
            auto_create_sections=False,
            tenant_limit=2000,
        )

    assert result.listing_id == 555
    assert result.listing_url == "https://www.etsy.com/listing/555"
    # DRAFT ONLY: state is never set.
    assert "state" not in fake.last_listing
    # Section resolved from the theme rule to the existing section.
    assert fake.last_listing["shop_section_id"] == 10
    # Images uploaded in rank order: thumbnail rank 1, size chart second-to-last.
    assert fake.uploaded == [(1, "t.jpg"), (2, "c.jpg"), (3, "e.jpg")]
    # Sizes applied (taxonomy supports Size) -> one product per size.
    assert result.sizes_applied is True
    assert len(fake.inventory["products"]) == 2

    async with async_sm() as s:
        row = await s.get(GeneratedContent, content_id)
        assert row.etsy_listing_id == 555
        snaps = await s.execute(
            select(ListingSnapshot).where(ListingSnapshot.listing_id == 555)
        )
        snap = snaps.scalars().first()
        assert snap is not None and snap.job_id == job_id
        assert snap.payload["operation"] == "create_draft"


async def test_publish_writes_sku_to_every_product_and_marks_draft(
    async_sm: async_sessionmaker,
) -> None:
    """A2: the SKU must land in ``products[].sku`` for every variation. A4: the
    listing is recorded as a DRAFT so the UI links it to Shop Manager."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties={"results": [{"name": "Size"}]})
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"thumb", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            size_config=SizeConfig("Size", ["S", "M"], {}),
            theme="abstract",
            tenant_limit=2000,
        )

    products = fake.inventory["products"]
    assert products, "inventory must contain products"
    assert all("BR5475" in p["sku"] for p in products)

    async with async_sm() as s:
        row = await s.get(GeneratedContent, content_id)
        assert row.etsy_listing_state == "draft"


def test_link_for_picks_edit_url_for_draft_and_public_for_active() -> None:
    from app.etsy.publisher import link_for, listing_edit_url, listing_url

    assert link_for(555, "draft") == listing_edit_url(555)
    assert link_for(555, None) == listing_edit_url(555)  # null == draft
    assert link_for(555, "active") == listing_url(555)
    assert "your/shops/me/listing-editor/edit/555" in listing_edit_url(555)


async def test_publish_without_size_support_single_product(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties={"results": []})  # taxonomy has no Size property
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        result = await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"thumb", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            size_config=SizeConfig("Size", ["S", "M"], {}),
            theme="abstract",
            tenant_limit=2000,
        )
    assert result.sizes_applied is False
    assert len(fake.inventory["products"]) == 1
    assert fake.uploaded == [(1, "t.jpg")]


async def test_publish_blocked_by_compliance(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm, blocking=True)
    fake = FakeEtsy()
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(PublishBlocked):
            await publish_content(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                sku="BR5475",
                thumbnail=PublishImage(b"thumb", "t.jpg"),
                client=fake,
                access_token="tok",
                config=CONFIG,
                tenant_limit=2000,
            )
    assert fake.calls == []  # nothing was sent to Etsy
    async with async_sm() as s:
        assert (await s.get(GeneratedContent, content_id)).etsy_listing_id is None
