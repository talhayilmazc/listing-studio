"""refresh_profile / sync_shop_listings worker jobs (Etsy client faked)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    ListingProfile,
    ShopListingCache,
    Tenant,
)
from app.workers import profiles as worker


class FakeService:
    """Stands in for ConnectionService: returns the seeded connection + a token."""

    def __init__(self, tenant_id: uuid.UUID) -> None:
        self._tenant_id = tenant_id

    async def get_active(self, session, tenant_id):  # noqa: ANN001
        rows = await session.execute(
            select(EtsyConnection).where(EtsyConnection.tenant_id == tenant_id)
        )
        return rows.scalars().first()

    async def get_valid_access_token(self, session, connection) -> str:  # noqa: ANN001
        return "tok"


class FakeEtsy:
    def __init__(self) -> None:
        self.listings_calls: list[str] = []

    async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
        return {
            "listing_id": listing_id,
            "taxonomy_id": 2078,
            "price": {"amount": 2599, "divisor": 100, "currency_code": "USD"},
            "who_made": "i_did",
            "when_made": "made_to_order",
            "description": "Old Title\nSize chart\nShips fast.",
        }

    async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict[str, Any]:
        return {"products": [{"sku": "OLD", "offerings": [], "property_values": []}]}

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        return {"results": [{"listing_image_id": 900, "rank": 1, "url_fullxfull": "u"}]}

    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        self.listings_calls.append(state)
        if state == "active":
            return {"results": [{"listing_id": 1, "title": "A", "state": "active"}]}
        return {"results": [{"listing_id": 2, "title": "B", "state": "draft"}]}


async def _seed(sm: async_sessionmaker, *, with_profile: bool = True):
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x", daily_quota=2000)
        s.add(tenant)
        await s.flush()
        s.add(
            EtsyConnection(
                tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900, shop_id=900
            )
        )
        profile_id = None
        if with_profile:
            profile = ListingProfile(
                tenant_id=tenant.id, name="Tee", reference_listing_id=111
            )
            s.add(profile)
            await s.flush()
            profile_id = profile.id
        await s.commit()
        return tenant.id, profile_id


def _patch(monkeypatch, tenant_id, fake_etsy) -> None:
    monkeypatch.setattr(worker, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(worker, "_build_client", lambda ctx, http, settings: fake_etsy)


async def test_refresh_profile_populates_cached_payload(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, profile_id = await _seed(async_sm)
    fake = FakeEtsy()
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    result = await worker.refresh_profile(ctx, str(profile_id))
    assert result == "refreshed"

    async with async_sm() as s:
        profile = await s.get(ListingProfile, profile_id)
        assert profile.updated_at is not None
        payload = profile.cached_payload
        assert payload["taxonomy_id"] == 2078
        assert payload["price"] == 25.99
        assert payload["description"].startswith("Old Title")
        assert payload["images"][0]["listing_image_id"] == 900


async def test_sync_shop_listings_caches_active_and_draft(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    fake = FakeEtsy()
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    result = await worker.sync_shop_listings(ctx, str(tenant_id))
    assert result == "synced:2"
    assert fake.listings_calls == ["active", "draft"]

    async with async_sm() as s:
        rows = await s.execute(
            select(ShopListingCache).where(ShopListingCache.tenant_id == tenant_id)
        )
        cached = {r.listing_id: r.payload["state"] for r in rows.scalars()}
        assert cached == {1: "active", 2: "draft"}
