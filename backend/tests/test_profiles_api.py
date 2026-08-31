"""Profile + shop-listing endpoint tests (no real Etsy calls)."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.db.base import Base
from app.db.models import (
    ListingProfile,
    ShopListingCache,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)

DEV_EMAIL = "dev@localhost"


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
        tenant = Tenant(email=DEV_EMAIL, password_hash="!", daily_quota=2000)
        s.add(tenant)
        await s.commit()
        tenant_id = tenant.id

    enqueuer = StubEnqueuer()

    async def _session():
        async with sm() as s:
            yield s

    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_enqueuer] = lambda: enqueuer

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield {"client": ac, "sm": sm, "enqueuer": enqueuer, "tenant_id": tenant_id}
    await engine.dispose()


async def test_create_profile_enqueues_refresh(ctx) -> None:
    res = await ctx["client"].post(
        "/api/profiles",
        json={"name": "Standard Tee", "reference_listing_id": 111, "content_template": "apparel"},
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
    assert ("detect_profiles", (str(ctx["tenant_id"]),)) in ctx["enqueuer"].calls

    # ...a detected (unconfirmed) profile is confirmed by the seller before use.
    async with ctx["sm"]() as s:
        profile = ListingProfile(
            tenant_id=ctx["tenant_id"],
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
            "/api/profiles", json={"name": "Tee", "reference_listing_id": 222}
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
            "/api/profiles", json={"name": "Tee", "reference_listing_id": 222}
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
    assert ctx["enqueuer"].calls == [("sync_shop_listings", (str(ctx["tenant_id"]),))]


async def test_shop_listings_returns_cached_rows(ctx) -> None:
    async with ctx["sm"]() as s:
        s.add(
            ShopListingCache(
                tenant_id=ctx["tenant_id"],
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


async def test_profile_model_defaults_to_apparel(ctx) -> None:
    async with ctx["sm"]() as s:
        p = ListingProfile(tenant_id=ctx["tenant_id"], name="X", reference_listing_id=7)
        s.add(p)
        await s.commit()
        await s.refresh(p)
        assert p.content_template == "apparel"  # apparel is the model default now


async def test_set_and_clear_size_chart_profile(ctx) -> None:
    async with ctx["sm"]() as s:
        batch = UploadBatch(
            tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=1
        )
        profile = ListingProfile(tenant_id=ctx["tenant_id"], name="Charts", reference_listing_id=5)
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


async def test_use_listing_as_profile_creates_and_enqueues(ctx) -> None:
    async with ctx["sm"]() as s:
        s.add(
            ShopListingCache(
                tenant_id=ctx["tenant_id"],
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
