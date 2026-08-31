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
    listing_edit_url,
    listing_url,
    publish_content,
    publish_live,
)
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

# Reference-listing payload the publisher copies from (A3 / B): two size variations,
# the seller's own taxonomy, price and fulfilment.
REFERENCE = {
    "taxonomy_id": 2078,
    "price": 25.99,
    "who_made": "i_did",
    "when_made": "made_to_order",
    "is_supply": False,
    "shipping_profile_id": 55,
    "production_partner_ids": [7],
    "listing_type": "physical",
    "inventory_products": [
        {
            "sku": "OLD-S",
            "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}
            ],
        },
        {
            "sku": "OLD-M",
            "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}
            ],
        },
    ],
}


class FakeEtsy:
    def __init__(self, *, sections=None, properties=None) -> None:
        self.calls: list[str] = []
        self._sections = sections if sections is not None else {"results": []}
        self._properties = properties if properties is not None else {"results": []}
        self.last_listing: dict[str, Any] | None = None
        self.inventory: dict[str, Any] | None = None
        self.uploaded: list[tuple[int, str]] = []
        self.created_section_title: str | None = None
        self.last_update: dict[str, Any] | None = None

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

    async def update_listing(self, shop_id: int, listing_id: int, *, updates: dict[str, Any], **_: Any):
        self.calls.append("update_listing")
        self.last_update = updates
        return {}

    async def upload_listing_image(
        self,
        shop_id,
        listing_id,
        *,
        rank,
        image_bytes=None,
        filename=None,
        listing_image_id=None,
        **_,
    ):
        # Record bytes uploads by filename; copied reference images by their id.
        self.uploaded.append((rank, filename if listing_image_id is None else listing_image_id))
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


async def test_publish_copies_reference_and_snapshots(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm, taxonomy_id=None)
    fake = FakeEtsy(sections={"results": [{"shop_section_id": 10, "title": "4th of July"}]})
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
            fixed_image_ids=[901],  # a reference image copied by id (B3)
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=REFERENCE,
            theme="patriotic eagle",
            auto_create_sections=False,
            tenant_limit=2000,
        )

    assert result.listing_id == 555
    # A draft links to Shop Manager, not the public URL (A4).
    assert result.listing_url == listing_edit_url(555)
    # DRAFT ONLY: state is never set.
    assert "state" not in fake.last_listing
    # Category/price/fulfilment copied VERBATIM from the reference (A3), not CONFIG.
    assert fake.last_listing["taxonomy_id"] == 2078
    assert fake.last_listing["price"] == 25.99
    assert fake.last_listing["type"] == "physical"
    assert fake.last_listing["shipping_profile_id"] == 55
    assert fake.last_listing["production_partner_ids"] == [7]
    # Section resolved from the theme rule to the existing section.
    assert fake.last_listing["shop_section_id"] == 10
    # Generated images first (thumbnail rank 1), then the fixed reference image by id.
    assert fake.uploaded == [(1, "t.jpg"), (2, "e.jpg"), (3, 901)]
    # Reference variation structure preserved -> one product per size.
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
    fake = FakeEtsy()
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
            reference=REFERENCE,
            theme="abstract",
            tenant_limit=2000,
        )

    products = fake.inventory["products"]
    assert products, "inventory must contain products"
    assert all(p["sku"] == "BR5475" for p in products)  # our SKU on every variation

    async with async_sm() as s:
        row = await s.get(GeneratedContent, content_id)
        assert row.etsy_listing_state == "draft"


async def test_publish_live_makes_draft_active(async_sm: async_sessionmaker) -> None:
    """E: an explicit Publish-now flips an existing draft to active via updateListing."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        content.etsy_listing_id = 555
        content.etsy_listing_state = "draft"
        await s.commit()

    fake = FakeEtsy()
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        result = await publish_live(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            client=fake,
            access_token="tok",
            tenant_limit=2000,
        )

    assert fake.last_update == {"state": "active"}
    assert result.listing_url == listing_url(555)  # active -> public URL
    async with async_sm() as s:
        row = await s.get(GeneratedContent, content_id)
        assert row.etsy_listing_state == "active"
        snaps = await s.execute(select(ListingSnapshot).where(ListingSnapshot.listing_id == 555))
        assert "publish_live" in [sn.payload["operation"] for sn in snaps.scalars()]


async def test_publish_live_blocked_by_compliance(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm, blocking=True)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        content.etsy_listing_id = 555
        content.etsy_listing_state = "draft"
        await s.commit()
    fake = FakeEtsy()
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(PublishBlocked):
            await publish_live(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                client=fake,
                access_token="tok",
                tenant_limit=2000,
            )
    assert fake.calls == []  # never touched Etsy


def test_link_for_picks_edit_url_for_draft_and_public_for_active() -> None:
    from app.etsy.publisher import link_for, listing_edit_url, listing_url

    assert link_for(555, "draft") == listing_edit_url(555)
    assert link_for(555, None) == listing_edit_url(555)  # null == draft
    assert link_for(555, "active") == listing_url(555)
    assert "your/shops/me/listing-editor/edit/555" in listing_edit_url(555)


async def test_publish_reference_without_variations_single_product(
    async_sm: async_sessionmaker,
) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    reference = {**REFERENCE, "inventory_products": []}  # reference has no variations
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
            reference=reference,
            theme="abstract",
            tenant_limit=2000,
        )
    assert result.sizes_applied is False
    assert len(fake.inventory["products"]) == 1
    assert fake.inventory["products"][0]["sku"] == "BR5475"
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
                reference=REFERENCE,
                tenant_limit=2000,
            )
    assert fake.calls == []  # nothing was sent to Etsy
    async with async_sm() as s:
        assert (await s.get(GeneratedContent, content_id)).etsy_listing_id is None
