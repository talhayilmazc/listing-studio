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
    replace_listing_images,
)
from tests.support import VALID_TITLE

CONFIG = PublishConfig(quantity=999)

# Reference-listing payload the publisher copies from (A3 / B): two size variations,
# the seller's own taxonomy, price and fulfilment.
REFERENCE = {
    "taxonomy_id": 2078,
    "price": 25.99,
    "who_made": "i_did",
    "when_made": "made_to_order",
    "is_supply": False,
    "shipping_profile_id": 55,
    "return_policy_id": 88,
    "readiness_state_id": 42,
    "production_partner_ids": [7],
    "should_auto_renew": True,
    "is_customizable": True,
    "is_personalizable": False,
    "processing_min": 1,
    "processing_max": 3,
    "price_on_property": [200],  # price varies on the Size property
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
    def __init__(self, *, sections=None, properties=None, readback_taxonomy=None) -> None:
        self.calls: list[str] = []
        self._sections = sections if sections is not None else {"results": []}
        self._properties = properties if properties is not None else {"results": []}
        # If set, get_listing returns this taxonomy (to simulate Etsy storing a
        # different/wrong category); otherwise it echoes what was submitted.
        self._readback_taxonomy = readback_taxonomy
        self.last_listing: dict[str, Any] | None = None
        self.inventory: dict[str, Any] | None = None
        self.uploaded: list[tuple[int, str]] = []
        self.created_section_title: str | None = None
        self.last_update: dict[str, Any] | None = None
        self.deleted: list[int] = []
        self.properties_set: list[dict[str, Any]] = []

    async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
        self.calls.append("get_listing")
        taxonomy = (
            self._readback_taxonomy
            if self._readback_taxonomy is not None
            else (self.last_listing or {}).get("taxonomy_id")
        )
        return {"listing_id": listing_id, "taxonomy_id": taxonomy}

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

    async def delete_listing_image(self, shop_id: int, listing_id: int, image_id: int, **_: Any):
        self.deleted.append(image_id)
        return {}

    async def update_listing_property(
        self, shop_id: int, listing_id: int, property_id: int, **kw: Any
    ):
        self.properties_set.append(
            {"property_id": property_id, "value_ids": kw.get("value_ids"), "values": kw.get("values")}
        )
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
    # All remaining settings copied from the reference, none from config (v4 §C).
    assert fake.last_listing["who_made"] == "i_did"
    assert fake.last_listing["when_made"] == "made_to_order"
    assert fake.last_listing["return_policy_id"] == 88
    # readiness_state_id (processing profile) is mandatory for physical listings (v4 §A).
    assert fake.last_listing["readiness_state_id"] == 42
    assert fake.last_listing["should_auto_renew"] is True
    assert fake.last_listing["is_customizable"] is True
    assert fake.last_listing["is_personalizable"] is False  # False copied, not dropped
    # Raw processing_min/max are superseded by readiness_state_id and not sent.
    assert "processing_min" not in fake.last_listing
    # Section resolved from the theme rule to the existing section.
    assert fake.last_listing["shop_section_id"] == 10
    # Generated images first (thumbnail rank 1), then the fixed reference image by id.
    assert fake.uploaded == [(1, "t.jpg"), (2, "e.jpg"), (3, 901)]
    # Reference variation structure preserved -> one product per size.
    assert result.sizes_applied is True
    assert len(fake.inventory["products"]) == 2
    # price_on_property carried through so Etsy accepts the size-varying prices.
    assert fake.inventory["price_on_property"] == [200]
    # Every offering carries a readiness_state_id (listing-level fallback here).
    assert all(
        o["readiness_state_id"] == 42
        for p in fake.inventory["products"]
        for o in p["offerings"]
    )

    async with async_sm() as s:
        row = await s.get(GeneratedContent, content_id)
        assert row.etsy_listing_id == 555
        snaps = await s.execute(
            select(ListingSnapshot).where(ListingSnapshot.listing_id == 555)
        )
        snap = snaps.scalars().first()
        assert snap is not None and snap.job_id == job_id
        assert snap.payload["operation"] == "create_draft"
    assert "get_listing" in fake.calls  # taxonomy read-back happened (v4 §A)


async def test_comfort_colors_profile_maps_to_that_section(async_sm: async_sessionmaker) -> None:
    """v3 §F rule 1: a Comfort Colors profile always uses the shop's CC section,
    deterministically — even when a theme rule would match a different section."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(
        sections={
            "results": [
                {"shop_section_id": 30, "title": "Comfort Colors"},
                {"shop_section_id": 10, "title": "4th of July"},
            ]
        }
    )
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=REFERENCE,
            theme="patriotic eagle",  # would match "4th of July" by theme
            profile_name="Comfort Colors Tee",
            tenant_limit=2000,
        )
    assert fake.last_listing["shop_section_id"] == 30  # CC rule wins over theme


async def test_comfort_colors_profile_without_section_leaves_it_unset(
    async_sm: async_sessionmaker,
) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(sections={"results": []})  # shop has no Comfort Colors section
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=REFERENCE,
            theme="patriotic eagle",
            profile_name="Comfort Colors",
            tenant_limit=2000,
        )
    assert "shop_section_id" not in fake.last_listing  # not created (auto-create off)


async def test_publish_always_sends_physical_type(async_sm: async_sessionmaker) -> None:
    """v4 §A: cached_payload never stores a listing type, yet the draft is always
    physical (a download type forces the Digital-files category)."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    reference = {k: v for k, v in REFERENCE.items() if k != "listing_type"}
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=reference,
            theme="x",
            tenant_limit=2000,
        )
    assert fake.last_listing["type"] == "physical"
    assert fake.last_listing["taxonomy_id"] == 2078  # from the reference, not CONFIG


async def test_publish_fails_when_readiness_state_id_missing(async_sm: async_sessionmaker) -> None:
    """v4 §A: readiness_state_id is mandatory for physical listings — fail, no default."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    reference = {k: v for k, v in REFERENCE.items() if k != "readiness_state_id"}
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(ValueError, match="readiness_state_id"):
            await publish_content(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                sku="BR5475",
                thumbnail=PublishImage(b"t", "t.jpg"),
                client=fake,
                access_token="tok",
                config=CONFIG,
                reference=reference,
                theme="x",
                tenant_limit=2000,
            )
    assert fake.last_listing is None  # never created a draft


async def test_publish_fails_when_stored_taxonomy_differs(async_sm: async_sessionmaker) -> None:
    """v4 §A: if Etsy stored a different category (e.g. Digital), fail loudly."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(readback_taxonomy=999)  # Etsy stored a different category
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(ValueError, match="taxonomy"):
            await publish_content(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                sku="BR5475",
                thumbnail=PublishImage(b"t", "t.jpg"),
                client=fake,
                access_token="tok",
                config=CONFIG,
                reference=REFERENCE,
                theme="x",
                tenant_limit=2000,
            )


REQUIRED_NECKLINE = {
    "results": [
        {
            "property_id": 100,
            "property_name": "Neckline",
            "is_required": True,
            "possible_values": [{"value_id": 11, "name": "Crew Neck"}],
        }
    ]
}


async def test_publish_applies_required_attribute_from_reference(
    async_sm: async_sessionmaker,
) -> None:
    """v4 §B: a required clothing attribute is copied from the reference listing."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties=REQUIRED_NECKLINE)
    reference = {**REFERENCE, "attributes": [{"property_id": 100, "value_ids": [11], "values": ["Crew Neck"]}]}
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=reference,
            theme="x",
            tenant_limit=2000,
        )
    assert fake.properties_set == [{"property_id": 100, "value_ids": [11], "values": ["Crew Neck"]}]


async def test_publish_applies_required_attribute_from_vision(
    async_sm: async_sessionmaker,
) -> None:
    """v4 §B: when the reference lacks it, the attribute comes from the mockup vision."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties=REQUIRED_NECKLINE)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        await publish_content(
            s,
            job_id=job_id,
            content=content,
            connection=conn,
            sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake,
            access_token="tok",
            config=CONFIG,
            reference=REFERENCE,  # no attributes
            vision={"neckline": "crew neck"},
            theme="x",
            tenant_limit=2000,
        )
    assert fake.properties_set == [{"property_id": 100, "value_ids": [11], "values": ["Crew Neck"]}]


async def test_publish_fails_when_required_attribute_undetermined(
    async_sm: async_sessionmaker,
) -> None:
    """v4 §B: an unfillable required attribute fails the job (no random default)."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties=REQUIRED_NECKLINE)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(ValueError, match="Neckline"):
            await publish_content(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                sku="BR5475",
                thumbnail=PublishImage(b"t", "t.jpg"),
                client=fake,
                access_token="tok",
                config=CONFIG,
                reference=REFERENCE,  # no attributes
                vision={},  # nothing from vision either
                theme="x",
                tenant_limit=2000,
            )
    assert fake.properties_set == []  # never set a guessed value


async def test_publish_requires_reference_taxonomy_no_default(async_sm: async_sessionmaker) -> None:
    """v4 §A: no code path selects a category — a profile without taxonomy fails."""
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    reference = {k: v for k, v in REFERENCE.items() if k != "taxonomy_id"}
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        conn = await s.get(EtsyConnection, conn_id)
        with pytest.raises(ValueError, match="taxonomy_id"):
            await publish_content(
                s,
                job_id=job_id,
                content=content,
                connection=conn,
                sku="BR5475",
                thumbnail=PublishImage(b"t", "t.jpg"),
                client=fake,
                access_token="tok",
                config=CONFIG,
                reference=reference,
                theme="x",
                tenant_limit=2000,
            )
    assert fake.last_listing is None  # never created a draft -> no default category


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


async def test_replace_listing_images_swaps_artwork_keeps_charts(
    async_sm: async_sessionmaker,
) -> None:
    """B4: delete artwork, keep + re-rank size charts, refresh copy, never touch state."""
    tenant_id, _conn_id, _content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        result = await replace_listing_images(
            s,
            job_id=job_id,
            listing_id=999,
            shop_id=900,
            tenant_id=tenant_id,
            client=fake,
            access_token="tok",
            tenant_limit=2000,
            existing_listing={"listing_id": 999, "description": "Old\n\nBody", "state": "active"},
            keep_image_ids=[900],  # a size chart to retain
            delete_image_ids=[801, 802],  # artwork to delete
            new_images=[PublishImage(b"t", "t.jpg"), PublishImage(b"e", "e.jpg")],
            new_title="Brand New Title",
            new_tags=["shirt", "tag1"],
            new_description="Brand New Title\n\nBody",
        )

    assert (result.deleted, result.added, result.kept) == (2, 2, 1)
    assert fake.deleted == [801, 802]
    # New photos ranked 1,2; the retained chart re-ranked to 3 (after the new photos).
    assert fake.uploaded == [(1, "t.jpg"), (2, "e.jpg"), (3, 900)]
    assert fake.last_update == {
        "title": "Brand New Title",
        "description": "Brand New Title\n\nBody",
        "tags": ["shirt", "tag1"],
    }
    assert "state" not in fake.last_update  # state is never touched (Task 3)

    async with async_sm() as s:
        snaps = await s.execute(select(ListingSnapshot).where(ListingSnapshot.listing_id == 999))
        snap = snaps.scalars().first()
        assert snap is not None and snap.payload["operation"] == "replace_images"


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
