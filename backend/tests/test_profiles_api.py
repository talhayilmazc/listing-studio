"""Profile + shop-listing endpoint tests (no real Etsy calls)."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    ListingProfile,
    ShopListingCache,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)

OWNER_EMAIL = "owner@example.com"


class StubEnqueuer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def enqueue(self, function: str, *args) -> None:
        self.calls.append((function, args))


@pytest_asyncio.fixture()
async def ctx() -> AsyncIterator[dict]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        tenant = Tenant(email=OWNER_EMAIL, password_hash="!", daily_quota=2000)
        s.add(tenant)
        await s.flush()
        shop = EtsyConnection(
            tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900, shop_id=900
        )
        s.add(shop)
        await s.commit()
        tenant_id = tenant.id
        shop_id = shop.id

    enqueuer = StubEnqueuer()

    async def _session():
        async with sm() as s:
            yield s

    from app.core.sessions import SessionStore
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_enqueuer] = lambda: enqueuer

    fake_redis = FakeAsyncRedis()
    app.dependency_overrides[deps.get_redis] = lambda: fake_redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(fake_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as ac:
        authenticate(ac, await open_session(fake_redis, tenant_id))
        yield {
            "client": ac,
            "sm": sm,
            "enqueuer": enqueuer,
            "tenant_id": tenant_id,
            "shop": shop_id,
            "redis": fake_redis,
            "app": app,
        }
    await engine.dispose()


async def test_create_profile_enqueues_refresh(ctx) -> None:
    res = await ctx["client"].post(
        "/api/profiles",
        json={"connection_id": str(ctx["shop"]), "name": "Standard Tee", "reference_listing_id": 111, "content_template": "apparel"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Standard Tee"
    assert body["is_fresh"] is False  # payload not fetched yet
    assert body["source"] == "manual" and body["confirmed"] is True  # hand-made -> confirmed
    assert ctx["enqueuer"].calls == [("refresh_profile", (body["id"],))]


async def test_detect_and_confirm_flow(ctx) -> None:
    # Detection is enqueued...
    res = await ctx["client"].post("/api/shop/detect-profiles")
    assert res.status_code == 202
    assert ("detect_profiles", (str(ctx["shop"]),)) in ctx["enqueuer"].calls

    # ...a detected (unconfirmed) profile is confirmed by the seller before use.
    async with ctx["sm"]() as s:
        profile = ListingProfile(
            tenant_id=ctx["tenant_id"],
            connection_id=ctx["shop"],
            name="Standard Tee",
            reference_listing_id=555,
            source="detected",
            confirmed=False,
        )
        s.add(profile)
        await s.commit()
        pid = profile.id
    body = (await ctx["client"].post(f"/api/profiles/{pid}/confirm")).json()
    assert body["confirmed"] is True and body["source"] == "detected"


async def test_list_get_patch_delete_profile(ctx) -> None:
    created = (
        await ctx["client"].post(
            "/api/profiles", json={"connection_id": str(ctx["shop"]), "name": "Tee", "reference_listing_id": 222}
        )
    ).json()
    pid = created["id"]

    listed = (await ctx["client"].get("/api/profiles")).json()
    assert [p["id"] for p in listed] == [pid]

    patched = (
        await ctx["client"].patch(
            f"/api/profiles/{pid}", json={"content_template": "apparel", "fixed_image_ids": [900]}
        )
    ).json()
    assert patched["content_template"] == "apparel"
    assert patched["fixed_image_ids"] == [900]

    assert (await ctx["client"].delete(f"/api/profiles/{pid}")).status_code == 204
    assert (await ctx["client"].get(f"/api/profiles/{pid}")).status_code == 404


async def test_refresh_endpoint_reenqueues(ctx) -> None:
    pid = (
        await ctx["client"].post(
            "/api/profiles", json={"connection_id": str(ctx["shop"]), "name": "Tee", "reference_listing_id": 222}
        )
    ).json()["id"]
    ctx["enqueuer"].calls.clear()
    res = await ctx["client"].post(f"/api/profiles/{pid}/refresh")
    assert res.status_code == 200
    assert ctx["enqueuer"].calls == [("refresh_profile", (pid,))]


async def test_profile_is_fresh_reflects_cached_payload(ctx) -> None:
    async with ctx["sm"]() as s:
        profile = ListingProfile(
            tenant_id=ctx["tenant_id"],
            connection_id=ctx["shop"],
            name="Fresh",
            reference_listing_id=333,
            cached_payload={"description": "x", "taxonomy_id": 1, "images": []},
            updated_at=datetime.now(timezone.utc),
        )
        s.add(profile)
        await s.commit()
        pid = profile.id
    body = (await ctx["client"].get(f"/api/profiles/{pid}")).json()
    assert body["is_fresh"] is True


# --- Shop listings ----------------------------------------------------------
async def test_shop_listings_empty_triggers_sync(ctx) -> None:
    res = await ctx["client"].get("/api/shop/listings")
    assert res.status_code == 200
    body = res.json()
    assert body["listings"] == []
    assert body["stale"] is True
    assert ctx["enqueuer"].calls == [("sync_shop_listings", (str(ctx["shop"]),))]


async def test_shop_listings_returns_cached_rows(ctx) -> None:
    async with ctx["sm"]() as s:
        s.add(
            ShopListingCache(
                tenant_id=ctx["tenant_id"],
                connection_id=ctx["shop"],
                listing_id=777,
                payload={
                    "listing_id": 777,
                    "title": "My Tee",
                    "state": "active",
                    "skus": ["BR5475"],
                    "url": "https://www.etsy.com/listing/777",
                    "images": [{"url_570xN": "https://img/570.jpg"}],
                },
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()
    body = (await ctx["client"].get("/api/shop/listings")).json()
    assert body["stale"] is False
    assert ctx["enqueuer"].calls == []  # fresh -> no sync
    row = body["listings"][0]
    assert row["listing_id"] == 777
    assert row["sku"] == "BR5475"
    assert row["thumbnail_url"] == "https://img/570.jpg"


async def test_shop_summary_counts_without_triggering_sync(ctx) -> None:
    """Counts come from the cache; unlike /shop/listings this never enqueues."""
    now = datetime.now(timezone.utc)
    this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=2, hour=12)
    long_ago = this_month - timedelta(days=400)

    rows = [
        (1, "active", this_month),
        (2, "active", this_month + timedelta(hours=5)),
        (3, "active", last_month),
        (4, "active", long_ago),
        (5, "draft", this_month),  # never published -> counts in neither month
    ]
    async with ctx["sm"]() as s:
        for listing_id, state, stamp in rows:
            s.add(
                ShopListingCache(
                    tenant_id=ctx["tenant_id"],
                    connection_id=ctx["shop"],
                    listing_id=listing_id,
                    payload={
                        "listing_id": listing_id,
                        "state": state,
                        "state_timestamp": int(stamp.timestamp()),
                    },
                    fetched_at=now,
                )
            )
        await s.commit()

    body = (await ctx["client"].get("/api/shop/summary")).json()
    assert ctx["enqueuer"].calls == []  # the whole point: no sync side effect

    assert body["total"] == 5
    assert body["active"] == 4
    assert body["draft"] == 1
    assert body["published_this_month"] == 2
    assert body["published_last_month"] == 1
    assert body["stale"] is False


async def test_shop_summary_stale_cache_still_does_not_sync(ctx) -> None:
    """A stale cache is reported as stale, but reading it must not enqueue."""
    res = await ctx["client"].get("/api/shop/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["stale"] is True
    assert body["fetched_at"] is None
    assert ctx["enqueuer"].calls == []


async def test_shop_listing_carries_state_timestamp(ctx) -> None:
    stamp = int(datetime.now(timezone.utc).timestamp())
    async with ctx["sm"]() as s:
        s.add(
            ShopListingCache(
                tenant_id=ctx["tenant_id"],
                connection_id=ctx["shop"],
                listing_id=778,
                payload={"listing_id": 778, "state": "active", "state_timestamp": stamp},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()
    row = (await ctx["client"].get("/api/shop/listings")).json()["listings"][0]
    assert row["state_timestamp"] == stamp


async def test_profile_model_defaults_to_apparel(ctx) -> None:
    async with ctx["sm"]() as s:
        p = ListingProfile(tenant_id=ctx["tenant_id"], connection_id=ctx["shop"], name="X", reference_listing_id=7)
        s.add(p)
        await s.commit()
        await s.refresh(p)
        assert p.content_template == "apparel"  # apparel is the model default now


async def test_set_and_clear_size_chart_profile(ctx) -> None:
    async with ctx["sm"]() as s:
        batch = UploadBatch(
            tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=1
        )
        profile = ListingProfile(tenant_id=ctx["tenant_id"], connection_id=ctx["shop"], name="Charts", reference_listing_id=5)
        s.add_all([batch, profile])
        await s.commit()
        batch_id, profile_id = batch.id, profile.id

    res = await ctx["client"].put(
        f"/api/batches/{batch_id}/size-chart-profile", json={"profile_id": str(profile_id)}
    )
    assert res.status_code == 200
    assert res.json()["size_chart_profile_id"] == str(profile_id)

    cleared = await ctx["client"].put(
        f"/api/batches/{batch_id}/size-chart-profile", json={"profile_id": None}
    )
    assert cleared.json()["size_chart_profile_id"] is None


async def test_per_group_profile_assignment_and_bulk(ctx) -> None:
    """v4 §E: bulk applies to all groups; a manual per-group choice is preserved."""
    async with ctx["sm"]() as s:
        batch = UploadBatch(
            tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=2
        )
        s.add(batch)
        await s.flush()
        for gk in ("A", "B"):
            s.add(
                Asset(
                    batch_id=batch.id,
                    tenant_id=ctx["tenant_id"],
                    original_filename=f"{gk}.png",
                    parsed_sku=gk,
                    group_key=gk,
                    storage_key="k",
                    status=AssetStatus.processed,
                    rank=1,
                )
            )
        p1 = ListingProfile(tenant_id=ctx["tenant_id"], connection_id=ctx["shop"], name="P1", reference_listing_id=1)
        p2 = ListingProfile(tenant_id=ctx["tenant_id"], connection_id=ctx["shop"], name="P2", reference_listing_id=2)
        s.add_all([p1, p2])
        await s.commit()
        batch_id, p1_id, p2_id = batch.id, str(p1.id), str(p2.id)

    base = f"/api/batches/{batch_id}/groups"
    groups = (await ctx["client"].get(base)).json()
    assert {g["group_key"] for g in groups} == {"A", "B"}
    assert all(g["profile_id"] is None for g in groups)

    # Bulk: assign P1 to all groups.
    await ctx["client"].put(base, json={"profile_id": p1_id})
    assert all(g["profile_id"] == p1_id for g in (await ctx["client"].get(base)).json())

    # Manually override group B -> P2.
    await ctx["client"].put(base, json={"group_key": "B", "profile_id": p2_id})
    # Bulk P1 again must NOT overwrite the manual B.
    await ctx["client"].put(base, json={"profile_id": p1_id})
    by_key = {g["group_key"]: g for g in (await ctx["client"].get(base)).json()}
    assert by_key["A"]["profile_id"] == p1_id
    assert by_key["B"]["profile_id"] == p2_id and by_key["B"]["manual"] is True


async def test_use_listing_as_profile_creates_and_enqueues(ctx) -> None:
    async with ctx["sm"]() as s:
        s.add(
            ShopListingCache(
                tenant_id=ctx["tenant_id"],
                connection_id=ctx["shop"],
                listing_id=888,
                payload={"listing_id": 888, "title": "Cool Hoodie"},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()
    res = await ctx["client"].post("/api/shop/listings/888/use-as-profile")
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Cool Hoodie"  # defaulted from the listing title
    assert body["reference_listing_id"] == 888
    assert ("refresh_profile", (body["id"],)) in ctx["enqueuer"].calls
    async with ctx["sm"]() as s:
        rows = await s.execute(select(ListingProfile).where(ListingProfile.reference_listing_id == 888))
        assert rows.scalars().first() is not None


async def test_expired_shop_listings_are_never_shown(ctx) -> None:
    """Past 6 hours a listing is re-fetched, not displayed (CLAUDE.md)."""
    now = datetime.now(timezone.utc)
    async with ctx["sm"]() as s:
        for listing_id, age in ((900, timedelta(hours=7)), (901, timedelta(minutes=5))):
            s.add(
                ShopListingCache(
                    tenant_id=ctx["tenant_id"],
                    connection_id=ctx["shop"],
                    listing_id=listing_id,
                    payload={"listing_id": listing_id, "state": "active", "state_timestamp": 1},
                    fetched_at=now - age,
                )
            )
        await s.commit()

    body = (await ctx["client"].get("/api/shop/listings")).json()
    assert [row["listing_id"] for row in body["listings"]] == [901]

    summary = (await ctx["client"].get("/api/shop/summary")).json()
    assert summary["total"] == 1 and summary["active"] == 1


async def test_reference_image_links_are_withheld_after_6_hours(ctx) -> None:
    """Displayed data follows the 6h limit even before retention strips it."""
    payload = {
        "taxonomy_id": 1,
        "images": [
            {"listing_image_id": 1, "rank": 1, "kind": "artwork", "url": "https://img/1.jpg"},
            {"listing_image_id": 2, "rank": 2, "kind": "size_chart", "url": "https://img/2.jpg"},
        ],
    }
    now = datetime.now(timezone.utc)
    async with ctx["sm"]() as s:
        for name, age in (("recent", timedelta(hours=2)), ("older", timedelta(hours=7))):
            s.add(
                ListingProfile(
                    tenant_id=ctx["tenant_id"],
                    connection_id=ctx["shop"],
                    name=name,
                    reference_listing_id=int(age.total_seconds()),
                    content_template="apparel",
                    cached_payload=payload,
                    updated_at=now - age,
                )
            )
        await s.commit()

    profiles = {p["name"]: p for p in (await ctx["client"].get("/api/profiles")).json()}
    recent, older = profiles["recent"], profiles["older"]

    assert recent["reference_images_expired"] is False
    assert [i["url"] for i in recent["reference_images"]] == ["https://img/1.jpg", "https://img/2.jpg"]

    # 7 hours: the links are withheld, the structure is not.
    assert older["reference_images_expired"] is True
    assert [i["url"] for i in older["reference_images"]] == [None, None]
    assert [i["kind"] for i in older["reference_images"]] == ["artwork", "size_chart"]
    # Still usable for generating and publishing: that follows the 24h limit.
    assert older["is_fresh"] is True
