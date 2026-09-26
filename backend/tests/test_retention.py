"""Retention of Etsy-sourced content (CLAUDE.md cache rules).

Every rule here is also a sentence in the Privacy Policy; these tests are what
keep that sentence true.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select

from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingSnapshot,
    ShopListingCache,
    UploadBatch,
    UploadBatchStatus,
)
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.auth_support import make_tenant

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


async def _seed(sm, email: str) -> dict:
    """One tenant with Etsy content at a range of ages, plus their own work."""
    tenant_id = await make_tenant(sm, email)
    async with sm() as s:
        conn = EtsyConnection(
            tenant_id=tenant_id,
            status=ConnectionStatus.active,
            # One Etsy user per shop, and a shop belongs to one account (v5 §E).
            etsy_user_id=uuid.uuid5(uuid.NAMESPACE_DNS, email).int % 10**9,
            shop_id=1,
            access_token_enc=b"enc-access",
            refresh_token_enc=b"enc-refresh",
        )
        s.add(conn)
        await s.flush()
        job = Job(
            tenant_id=tenant_id,
            connection_id=conn.id,
            type=JobType.update_listing,
            status=JobStatus.succeeded,
            payload={},
        )
        s.add(job)
        await s.flush()

        for listing_id, age in ((1, timedelta(days=91)), (2, timedelta(days=89))):
            s.add(
                ListingSnapshot(
                    tenant_id=tenant_id,
                    listing_id=listing_id,
                    job_id=job.id,
                    payload={"title": "before"},
                    taken_at=NOW - age,
                )
            )
        for listing_id, age in ((10, timedelta(hours=7)), (11, timedelta(hours=5))):
            s.add(
                ShopListingCache(
                    tenant_id=tenant_id,
                    connection_id=conn.id,
                    listing_id=listing_id,
                    payload={"listing_id": listing_id, "state": "active"},
                    fetched_at=NOW - age,
                )
            )
        stale = ListingProfile(
            tenant_id=tenant_id,
            connection_id=conn.id,
            name="stale",
            reference_listing_id=100,
            content_template="apparel",
            cached_payload={"price": 25.0, "images": []},
            fixed_image_ids=[7, 8],
            title_prefix="COMFORT COLORS",
            updated_at=NOW - timedelta(hours=25),
        )
        fresh = ListingProfile(
            tenant_id=tenant_id,
            connection_id=conn.id,
            name="fresh",
            reference_listing_id=101,
            content_template="apparel",
            cached_payload={"price": 30.0, "images": []},
            updated_at=NOW - timedelta(hours=23),
        )
        # The seller's own work, which is not Etsy content.
        batch = UploadBatch(tenant_id=tenant_id, status=UploadBatchStatus.ready, file_count=1)
        s.add_all([stale, fresh, batch])
        await s.flush()
        asset = Asset(
            tenant_id=tenant_id,
            batch_id=batch.id,
            original_filename="design.png",
            storage_key=f"{tenant_id}/x.png",
            status=AssetStatus.processed,
        )
        s.add(asset)
        await s.flush()
        s.add(
            GeneratedContent(
                tenant_id=tenant_id, batch_id=batch.id, asset_id=asset.id, title="t", tags=[]
            )
        )
        await s.commit()
        return {
            "tenant_id": tenant_id,
            "connection_id": conn.id,
            "stale_profile": stale.id,
            "fresh_profile": fresh.id,
        }


@pytest_asyncio.fixture()
async def world(async_sm):
    return {"sm": async_sm, "a": await _seed(async_sm, "a@example.com"), "b": await _seed(async_sm, "b@example.com")}


async def _count(sm, model, tenant_id: uuid.UUID) -> int:
    async with sm() as s:
        return await s.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id))


# --- time-based expiry ------------------------------------------------------
async def test_snapshots_expire_at_90_days(world) -> None:
    sm = world["sm"]
    async with sm() as s:
        await purge_expired_rows(s, now=NOW)
    async with sm() as s:
        kept = (await s.execute(select(ListingSnapshot.listing_id))).scalars().all()
    assert sorted(kept) == [2, 2]  # the 89-day copy survives for both tenants; 91 is gone


async def test_shop_listings_expire_at_6_hours(world) -> None:
    sm = world["sm"]
    async with sm() as s:
        await purge_expired_rows(s, now=NOW)
    async with sm() as s:
        kept = (await s.execute(select(ShopListingCache.listing_id))).scalars().all()
    assert sorted(kept) == [11, 11]


async def test_profile_payload_expires_at_24_hours_but_settings_stay(world) -> None:
    sm, a = world["sm"], world["a"]
    async with sm() as s:
        counts = await purge_expired_rows(s, now=NOW)
    assert counts["profile_payloads"] == 2  # one stale profile per tenant

    async with sm() as s:
        stale = await s.get(ListingProfile, a["stale_profile"])
        fresh = await s.get(ListingProfile, a["fresh_profile"])
    assert stale.cached_payload is None
    # The seller's own configuration is not Etsy content and is kept.
    assert stale.name == "stale"
    assert stale.title_prefix == "COMFORT COLORS"
    assert stale.fixed_image_ids == [7, 8]
    assert fresh.cached_payload == {"price": 30.0, "images": []}


async def test_purge_is_idempotent(world) -> None:
    sm = world["sm"]
    async with sm() as s:
        await purge_expired_rows(s, now=NOW)
    async with sm() as s:
        again = await purge_expired_rows(s, now=NOW)
    assert again == {
        "snapshots": 0,
        "shop_listings": 0,
        "profile_image_links": 0,
        "profile_payloads": 0,
        "sales_days": 0,
        "ad_spend": 0,
    }


# --- disconnect -------------------------------------------------------------
async def test_disconnect_deletes_all_etsy_content_for_that_tenant_only(world) -> None:
    from cryptography.fernet import Fernet

    from app.core.crypto import TokenCipher
    from app.etsy.connection import ConnectionService

    sm, a, b = world["sm"], world["a"], world["b"]
    service = ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="t")
    async with sm() as s:
        await service.disconnect(s, await s.get(EtsyConnection, a["connection_id"]))

    # Everything Etsy-sourced for that shop is gone at once — fresh or not —
    # including its profiles, which were built from its own listings (v5 §E).
    assert await _count(sm, ListingSnapshot, a["tenant_id"]) == 0
    assert await _count(sm, ShopListingCache, a["tenant_id"]) == 0
    assert await _count(sm, ListingProfile, a["tenant_id"]) == 0
    async with sm() as s:
        conn = await s.get(EtsyConnection, a["connection_id"])
    assert conn.status is ConnectionStatus.revoked
    assert conn.access_token_enc is None and conn.refresh_token_enc is None

    # A's own work survives: uploads and generated drafts are not Etsy's.
    assert await _count(sm, Asset, a["tenant_id"]) == 1
    assert await _count(sm, GeneratedContent, a["tenant_id"]) == 1

    # B is untouched.
    assert await _count(sm, ListingSnapshot, b["tenant_id"]) == 2
    assert await _count(sm, ShopListingCache, b["tenant_id"]) == 2


async def test_shop_purge_reports_what_it_removed(world) -> None:
    sm, a = world["sm"], world["a"]
    async with sm() as s:
        counts = await purge_shop_etsy_content(s, a["connection_id"])
        await s.commit()
    assert counts == {
        "snapshots": 2, "shop_listings": 2, "publications": 0, "profiles": 2, "sales_days": 0, "ad_spend": 0,
    }


# --- wiring -----------------------------------------------------------------
def test_retention_runs_on_a_schedule() -> None:
    from app.workers.settings import WorkerSettings

    names = [job.coroutine.__name__ for job in WorkerSettings.cron_jobs]
    assert "purge_expired" in names


# --- the two profile limits: displayed (6h) vs structural (24h) -------------
def _payload() -> dict:
    return {
        "taxonomy_id": 2078,
        "shipping_profile_id": 55,
        "price": 25.0,
        "inventory_products": [{"sku": "X", "offerings": []}],
        "description": "Reference description.",
        "images": [
            {
                "listing_image_id": 900,
                "rank": 1,
                "kind": "artwork",
                "url": "https://i.etsystatic.com/full-900.jpg",
                "display_url": "https://i.etsystatic.com/570-900.jpg",
            },
            {
                "listing_image_id": 901,
                "rank": 2,
                "kind": "size_chart",
                "url": "https://i.etsystatic.com/full-901.jpg",
                "display_url": "https://i.etsystatic.com/570-901.jpg",
            },
        ],
    }


async def _profile_aged(sm, tenant_id, hours: float) -> uuid.UUID:
    async with sm() as s:
        shop = (
            await s.execute(select(EtsyConnection.id).where(EtsyConnection.tenant_id == tenant_id))
        ).scalars().first()
        profile = ListingProfile(
            tenant_id=tenant_id,
            connection_id=shop,
            name=f"aged {hours}h",
            reference_listing_id=int(hours * 100),
            content_template="apparel",
            cached_payload=_payload(),
            fixed_image_ids=[901],
            updated_at=NOW - timedelta(hours=hours),
        )
        s.add(profile)
        await s.commit()
        return profile.id


async def test_image_links_are_stripped_after_6_hours_structure_kept(world) -> None:
    sm, a = world["sm"], world["a"]
    seven = await _profile_aged(sm, a["tenant_id"], 7)
    five = await _profile_aged(sm, a["tenant_id"], 5)

    async with sm() as s:
        counts = await purge_expired_rows(s, now=NOW)
    assert counts["profile_image_links"] == 1

    async with sm() as s:
        stripped = (await s.get(ListingProfile, seven)).cached_payload
        untouched = (await s.get(ListingProfile, five)).cached_payload

    # Past 6 hours: no link to any Etsy image survives in storage...
    for image in stripped["images"]:
        assert "url" not in image and "display_url" not in image
    # ...but what the service needs to build a draft is all still there.
    assert [(i["listing_image_id"], i["rank"], i["kind"]) for i in stripped["images"]] == [
        (900, 1, "artwork"),
        (901, 2, "size_chart"),
    ]
    for key in ("taxonomy_id", "shipping_profile_id", "price", "inventory_products", "description"):
        assert stripped[key] == _payload()[key], key

    assert untouched == _payload()  # under 6 hours: nothing changes


async def test_structural_data_is_cleared_after_24_hours(world) -> None:
    sm, a = world["sm"], world["a"]
    old = await _profile_aged(sm, a["tenant_id"], 25)
    async with sm() as s:
        await purge_expired_rows(s, now=NOW)
    async with sm() as s:
        profile = await s.get(ListingProfile, old)
    assert profile.cached_payload is None
    assert profile.fixed_image_ids == [901]  # the seller's choice outlives the data


async def test_stripping_is_idempotent(world) -> None:
    sm, a = world["sm"], world["a"]
    await _profile_aged(sm, a["tenant_id"], 7)
    async with sm() as s:
        await purge_expired_rows(s, now=NOW)
    async with sm() as s:
        again = await purge_expired_rows(s, now=NOW)
    assert again["profile_image_links"] == 0
