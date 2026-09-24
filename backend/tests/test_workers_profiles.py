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
            "title": "COMFORT COLORS Retro Frog Tee",
            "price": {"amount": 2599, "divisor": 100, "currency_code": "USD"},
            "who_made": "i_did",
            "when_made": "made_to_order",
            "description": "Old Title\nSize chart\nShips fast.",
        }

    async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict[str, Any]:
        return {"products": [{"sku": "OLD", "offerings": [], "property_values": []}]}

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        return {"results": [{"listing_image_id": 900, "rank": 1, "url_fullxfull": "u"}]}

    async def get_listing_properties(self, shop_id: int, listing_id: int, **_: Any) -> dict[str, Any]:
        return {"results": [{"property_id": 100, "property_name": "Neckline", "values": ["Crew Neck"]}]}

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
        connection = EtsyConnection(
            tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900, shop_id=900
        )
        s.add(connection)
        await s.flush()
        profile_id = None
        if with_profile:
            profile = ListingProfile(
                tenant_id=tenant.id, connection_id=connection.id, name="Tee", reference_listing_id=111
            )
            s.add(profile)
            await s.flush()
            profile_id = profile.id
        await s.commit()
        return tenant.id, profile_id


async def _shop_of(sm: async_sessionmaker, tenant_id) -> uuid.UUID:
    async with sm() as s:
        rows = await s.execute(select(EtsyConnection.id).where(EtsyConnection.tenant_id == tenant_id))
        return rows.scalars().first()


def _patch(monkeypatch, tenant_id, fake_etsy) -> None:
    monkeypatch.setattr(worker, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(worker, "_build_client", lambda ctx, http, settings, **_: fake_etsy)


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
        # refresh does NOT set the title prefix (that's derived per cluster at detect).
        assert profile.title_prefix is None


async def test_sync_shop_listings_caches_active_and_draft(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    fake = FakeEtsy()
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    result = await worker.sync_shop_listings(ctx, str(await _shop_of(async_sm, tenant_id)))
    assert result == "synced:2"
    assert fake.listings_calls == ["active", "draft"]

    async with async_sm() as s:
        rows = await s.execute(
            select(ShopListingCache).where(ShopListingCache.tenant_id == tenant_id)
        )
        cached = {r.listing_id: r.payload["state"] for r in rows.scalars()}
        assert cached == {1: "active", 2: "draft"}


class DetectFakeEtsy:
    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        if state != "active":
            return {"results": []}
        return {
            "results": [
                {
                    "listing_id": 10,
                    "title": "Comfort Colors Patriotic Tee",
                    "taxonomy_id": 100,
                    "price": {"amount": 2500, "divisor": 100},
                    "production_partners": [{"production_partner_id": 7, "partner_name": "Print Co"}],
                    "images": [{}, {}, {}],
                },
                {
                    "listing_id": 11,
                    "title": "Comfort Colors Retro Tee",
                    "taxonomy_id": 100,
                    "price": {"amount": 2600, "divisor": 100},
                    "production_partners": [{"production_partner_id": 7, "partner_name": "Print Co"}],
                    "images": [{}, {}],
                },
                {
                    "listing_id": 20,
                    "title": "Ceramic Coffee Mug Design",
                    "taxonomy_id": 200,
                    "price": {"amount": 1500, "divisor": 100},
                    "production_partners": [],
                    "images": [{}],
                },
            ]
        }

    async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict[str, Any]:
        if listing_id in (10, 11):
            return {"products": [{"property_values": [{"property_name": "Size"}]}]}
        return {"products": [{"property_values": []}]}

    async def get_seller_taxonomy_nodes(self, **_: Any) -> dict[str, Any]:
        # Tees (100) live under Clothing; the mug (200) does not.
        return {
            "results": [
                {"id": 1, "name": "Clothing", "children": [{"id": 100, "name": "Tops"}]},
                {"id": 2, "name": "Home & Living", "children": [{"id": 200, "name": "Mugs"}]},
            ]
        }


async def test_detect_profiles_clusters_and_creates_unconfirmed(
    async_sm: async_sessionmaker, monkeypatch, test_settings
) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    fake = DetectFakeEtsy()
    _patch(monkeypatch, tenant_id, fake)  # no LLM key in test settings -> heuristic naming
    test_settings.default_content_template = "digital_products"  # non-clothing fallback
    enqueued: list[tuple] = []

    async def _enqueue(func, *args):  # noqa: ANN001, ANN202
        enqueued.append((func, args))

    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "enqueue": _enqueue}
    result = await worker.detect_profiles(ctx, str(await _shop_of(async_sm, tenant_id)))
    assert result == "detected:2"  # tees cluster into one, the mug the other

    async with async_sm() as s:
        rows = await s.execute(
            select(ListingProfile).where(ListingProfile.tenant_id == tenant_id)
        )
        profiles = {p.reference_listing_id: p for p in rows.scalars()}
    assert set(profiles) == {10, 20}  # reference = most-complete listing per cluster
    assert all(p.source == "detected" and p.confirmed is False for p in profiles.values())
    # Apparel inferred from the Size variation; the mug is not apparel.
    assert profiles[10].content_template == "apparel"
    assert profiles[20].content_template == "digital_products"
    # Title prefix derived from the tee cluster's shared lead; none for the lone mug.
    assert profiles[10].title_prefix == "Comfort Colors"
    assert profiles[20].title_prefix == ""
    # Each new profile is queued for a payload refresh + image classification.
    assert sorted(a[0] for a in enqueued) == ["refresh_profile", "refresh_profile"]


async def test_detect_profiles_skips_existing_reference(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    async with async_sm() as s:  # a profile already references listing 10
        s.add(
            ListingProfile(
                tenant_id=tenant_id,
                connection_id=await _shop_of(async_sm, tenant_id),
                name="Tee",
                reference_listing_id=10,
            )
        )
        await s.commit()
    _patch(monkeypatch, tenant_id, DetectFakeEtsy())
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "enqueue": lambda *a: _noop()}
    result = await worker.detect_profiles(ctx, str(await _shop_of(async_sm, tenant_id)))
    assert result == "detected:1"  # only the mug is new


class PagedDetectEtsy(DetectFakeEtsy):
    """150 active tees, inventory attached to each page; the mug only on page two."""

    def __init__(self) -> None:
        self.offsets: list[int] = []
        self.includes: list[list[str]] = []
        self.inventory_reads = 0

    async def get_listings_by_shop(
        self, shop_id: int, *, state: str, limit: int = 100, offset: int = 0, includes=None, **_: Any
    ) -> dict[str, Any]:
        if state != "active":
            return {"results": [], "count": 0}
        self.offsets.append(offset)
        self.includes.append(list(includes or []))
        tee_inv = {"products": [{"property_values": [{"property_name": "Size"}]}]}
        rows = [
            {
                "listing_id": 1000 + i,
                "title": f"Comfort Colors Tee {i}",
                "taxonomy_id": 100,
                "price": {"amount": 2500, "divisor": 100},
                "production_partners": [],
                "images": [{}],
                "inventory": tee_inv,
            }
            for i in range(149)
        ] + [
            {
                "listing_id": 20,
                "title": "Ceramic Coffee Mug Design",
                "taxonomy_id": 200,
                "price": {"amount": 1500, "divisor": 100},
                "production_partners": [],
                "images": [{}],
                "inventory": {"products": [{"property_values": []}]},
            }
        ]
        return {"results": rows[offset : offset + limit], "count": len(rows)}

    async def get_listing_inventory(self, listing_id: int, **kw: Any) -> dict[str, Any]:
        self.inventory_reads += 1
        return await super().get_listing_inventory(listing_id, **kw)


async def test_detect_reads_every_page_with_inventory_attached(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    """v6 §B: detection is no longer capped at the first 100 active listings, and
    it takes inventory from the page instead of one extra request per listing."""
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    fake = PagedDetectEtsy()
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "enqueue": lambda *a: _noop()}
    result = await worker.detect_profiles(ctx, str(await _shop_of(async_sm, tenant_id)))

    assert fake.offsets == [0, 100]
    assert all("Inventory" in inc for inc in fake.includes)
    assert fake.inventory_reads == 0
    assert result == "detected:2"  # the mug on page two became its own profile
    async with async_sm() as s:
        refs = set(
            (
                await s.execute(
                    select(ListingProfile.reference_listing_id).where(
                        ListingProfile.tenant_id == tenant_id
                    )
                )
            ).scalars()
        )
    assert 20 in refs


async def _noop():
    return None


# --- title prefix on refresh (docs/duzeltmeler-v5.md §B) --------------------------
async def _with_shop_cache(sm: async_sessionmaker, tenant_id, *, age_hours: float = 0.0) -> None:
    from datetime import datetime, timedelta, timezone

    fetched = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    rows = [
        (501, "COMFORT COLORS Mushroom Tee"),
        (502, "Comfort Colors Cat Mom Shirt"),
        (503, "COMFORT COLORS Pumpkin Tee"),
    ]
    shop = await _shop_of(sm, tenant_id)
    async with sm() as s:
        for listing_id, title in rows:
            s.add(
                ShopListingCache(
                    tenant_id=tenant_id,
                    connection_id=shop,
                    listing_id=listing_id,
                    fetched_at=fetched,
                    payload={
                        "listing_id": listing_id,
                        "title": title,
                        "taxonomy_id": 2078,
                        "price": {"amount": 2599, "divisor": 100},
                    },
                )
            )
        await s.commit()


async def _set_prefix(sm: async_sessionmaker, profile_id, value) -> None:
    async with sm() as s:
        profile = await s.get(ListingProfile, profile_id)
        profile.title_prefix = value
        await s.commit()


async def _refresh_and_read_prefix(sm, monkeypatch, tenant_id, profile_id):
    _patch(monkeypatch, tenant_id, FakeEtsy())
    ctx = {"sessionmaker": sm, "bucket": None, "quota": None}
    assert await worker.refresh_profile(ctx, str(profile_id)) == "refreshed"
    async with sm() as s:
        return (await s.get(ListingProfile, profile_id)).title_prefix


async def test_refresh_fills_a_never_set_prefix_from_the_sellers_own_listings(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, profile_id = await _seed(async_sm)
    await _with_shop_cache(async_sm, tenant_id)

    # Reference title "COMFORT COLORS Retro Frog Tee"; its casing is kept.
    assert await _refresh_and_read_prefix(async_sm, monkeypatch, tenant_id, profile_id) == "COMFORT COLORS"


async def test_refresh_never_overwrites_a_prefix_the_seller_set_or_cleared(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, profile_id = await _seed(async_sm)
    await _with_shop_cache(async_sm, tenant_id)

    await _set_prefix(async_sm, profile_id, "Comfort Colors®")
    assert await _refresh_and_read_prefix(async_sm, monkeypatch, tenant_id, profile_id) == "Comfort Colors®"

    await _set_prefix(async_sm, profile_id, "")  # cleared on purpose
    assert await _refresh_and_read_prefix(async_sm, monkeypatch, tenant_id, profile_id) == ""


async def test_refresh_ignores_shop_data_past_its_six_hour_limit(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, profile_id = await _seed(async_sm)
    await _with_shop_cache(async_sm, tenant_id, age_hours=7)

    assert await _refresh_and_read_prefix(async_sm, monkeypatch, tenant_id, profile_id) is None


# --- the whole shop, and nothing that has left it (docs/duzeltmeler-v6.md §A4) --------
class PagedEtsy(FakeEtsy):
    """A shop with 250 active listings and 3 drafts, served 100 at a time."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: list[tuple[str, int]] = []

    async def get_listings_by_shop(self, shop_id: int, *, state: str, limit: int = 25, offset: int = 0, **_: Any):
        self.pages.append((state, offset))
        total = 250 if state == "active" else 3
        start = 1000 if state == "active" else 5000
        ids = range(start + offset, start + min(total, offset + limit))
        return {"count": total, "results": [{"listing_id": i, "state": state} for i in ids]}


async def test_sync_reads_every_page_of_a_large_shop(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    fake = PagedEtsy()
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    assert await worker.sync_shop_listings(ctx, str(await _shop_of(async_sm, tenant_id))) == "synced:253"
    assert fake.pages == [("active", 0), ("active", 100), ("active", 200), ("draft", 0)]


async def test_sync_drops_listings_that_have_left_the_shop(async_sm: async_sessionmaker, monkeypatch) -> None:
    from datetime import datetime, timezone

    tenant_id, _ = await _seed(async_sm, with_profile=False)
    shop = await _shop_of(async_sm, tenant_id)
    async with async_sm() as s:  # a draft that has since been deleted on Etsy
        s.add(ShopListingCache(tenant_id=tenant_id, connection_id=shop, listing_id=77,
                               payload={"listing_id": 77, "state": "draft"},
                               fetched_at=datetime.now(timezone.utc)))
        await s.commit()
    _patch(monkeypatch, tenant_id, FakeEtsy())
    await worker.sync_shop_listings({"sessionmaker": async_sm, "bucket": None, "quota": None}, str(shop))

    async with async_sm() as s:
        ids = set((await s.execute(select(ShopListingCache.listing_id))).scalars())
    assert ids == {1, 2}  # 77 is gone at once, not counted for up to six more hours
