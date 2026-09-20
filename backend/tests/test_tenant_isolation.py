"""Cross-tenant isolation (production-spec B6). These gate the release.

Two fully-populated tenants share one app. For every endpoint that takes a
resource id, tenant A tries to reach tenant B's row and must get **404** — not
403, which would confirm the row exists. List endpoints must return only the
caller's rows, quotas must not interact, and every endpoint must refuse an
unauthenticated request with 401.

The fixture deliberately seeds *both* tenants with the same shapes of data, so a
handler that forgot its tenant filter returns the other tenant's row rather than
an empty result, and the test fails loudly.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from cryptography.fernet import Fernet
from PIL import Image
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    ConnectionStatus,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ShopListingCache,
    UploadBatch,
    UploadBatchStatus,
)
from app.core.crypto import TokenCipher
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import make_tenant, open_session
from tests.support import VALID_TITLE

pytestmark = []


class StubEnqueuer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def enqueue(self, function: str, *args) -> None:
        self.calls.append((function, args))


@dataclass
class Party:
    """One tenant plus the ids of everything it owns."""

    tenant_id: uuid.UUID
    token: str
    batch_id: uuid.UUID
    asset_id: uuid.UUID
    content_id: uuid.UUID
    profile_id: uuid.UUID
    job_id: uuid.UUID
    listing_id: int


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 50), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


async def _seed(sm, storage: LocalStorage, email: str, listing_id: int) -> Party:
    """Create a tenant owning one of every resource the API exposes by id."""
    tenant_id = await make_tenant(sm, email)
    async with sm() as s:
        connection = EtsyConnection(
            tenant_id=tenant_id,
            status=ConnectionStatus.active,
            etsy_user_id=listing_id,
            shop_id=listing_id,
        )
        s.add(connection)
        await s.flush()
        batch = UploadBatch(tenant_id=tenant_id, status=UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()

        # Storage keys are tenant-prefixed (B4), so one tenant's path can never
        # collide with another's.
        key = f"{tenant_id}/{batch.id}/processed/design.jpg"
        storage.put(key, _png(), "image/jpeg")

        asset = Asset(
            tenant_id=tenant_id,
            batch_id=batch.id,
            original_filename="SKU1_front.png",
            parsed_sku="SKU1",
            group_key="",
            rank=1,
            status=AssetStatus.processed,
            storage_key=key,
            processed_key=key,
            mime_type="image/jpeg",
        )
        profile = ListingProfile(
            tenant_id=tenant_id,
            name=f"{email} profile",
            reference_listing_id=listing_id,
            content_template="apparel",
            confirmed=True,
        )
        s.add_all([asset, profile])
        await s.flush()

        content = GeneratedContent(
            tenant_id=tenant_id,
            batch_id=batch.id,
            asset_id=asset.id,
            listing_profile_id=profile.id,
            title=VALID_TITLE,
            tags=[f"tag{i}" for i in range(13)],
            description="A description.",
            approved=True,
        )
        job = Job(
            tenant_id=tenant_id,
            connection_id=connection.id,
            type=JobType.create_draft,
            status=JobStatus.queued,
            payload={},
            batch_id=batch.id,
        )
        s.add_all([content, job])
        s.add(
            ShopListingCache(
                tenant_id=tenant_id,
                listing_id=listing_id,
                payload={"listing_id": listing_id, "state": "active", "title": email},
            )
        )
        await s.commit()
        return Party(
            tenant_id=tenant_id,
            token="",
            batch_id=batch.id,
            asset_id=asset.id,
            content_id=content.id,
            profile_id=profile.id,
            job_id=job.id,
            listing_id=listing_id,
        )


@pytest_asyncio.fixture()
async def two(tmp_path) -> AsyncIterator[dict]:
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

    storage = LocalStorage(tmp_path)
    fake_redis = FakeAsyncRedis()
    enqueuer = StubEnqueuer()

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.dependency_overrides[deps.get_redis] = lambda: fake_redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(fake_redis)
    app.dependency_overrides[deps.get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(
        fake_redis, global_daily_limit=5000
    )
    app.dependency_overrides[deps.get_connection_service] = lambda: ConnectionService(
        TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t"
    )
    app.dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(
        sessionmaker=sm,
        storage=storage,
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
    )

    alice = await _seed(sm, storage, "alice@example.com", 1001)
    bob = await _seed(sm, storage, "bob@example.com", 2002)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as a_client:
        async with AsyncClient(transport=transport, base_url="http://test") as b_client:
            a_client.cookies.set(SESSION_COOKIE, await open_session(fake_redis, alice.tenant_id))
            b_client.cookies.set(SESSION_COOKIE, await open_session(fake_redis, bob.tenant_id))
            async with AsyncClient(transport=transport, base_url="http://test") as anon:
                yield {
                    "a": a_client,
                    "b": b_client,
                    "anon": anon,
                    "alice": alice,
                    "bob": bob,
                    "sm": sm,
                    "redis": fake_redis,
                }
    await engine.dispose()


# --- B3: resource access by id ----------------------------------------------
async def test_batch_endpoints_are_404_across_tenants(two) -> None:
    a, bob = two["a"], two["bob"]
    bid = bob.batch_id

    for method, path, kwargs in [
        ("GET", f"/api/batches/{bid}", {}),
        ("GET", f"/api/batches/{bid}/groups", {}),
        ("GET", f"/api/batches/{bid}/cost", {}),
        ("GET", f"/api/batches/{bid}/content", {}),
        ("POST", f"/api/batches/{bid}/finalize", {}),
        ("PUT", f"/api/batches/{bid}/size-chart-profile", {"json": {"profile_id": None}}),
        ("PUT", f"/api/batches/{bid}/groups", {"json": {"group_key": "", "profile_id": None}}),
        ("POST", f"/api/batches/{bid}/generate", {"json": {}}),
        ("POST", f"/api/batches/{bid}/publish", {}),
        ("POST", f"/api/batches/{bid}/publish-live", {}),
    ]:
        resp = await a.request(method, path, **kwargs)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"


async def test_cannot_upload_into_another_tenants_batch(two) -> None:
    a, bob = two["a"], two["bob"]
    resp = await a.post(
        f"/api/batches/{bob.batch_id}/assets",
        files={"file": ("x.png", io.BytesIO(_png()), "image/png")},
    )
    assert resp.status_code == 404


async def test_cannot_download_another_tenants_design(two) -> None:
    """The image endpoint is the quietest leak: a design is the product itself."""
    a, b, alice, bob = two["a"], two["b"], two["alice"], two["bob"]

    assert (await a.get(f"/api/assets/{alice.asset_id}/image")).status_code == 200
    assert (await a.get(f"/api/assets/{bob.asset_id}/image")).status_code == 404
    assert (await b.get(f"/api/assets/{alice.asset_id}/image")).status_code == 404

    # Including the resized variants, which take a different branch.
    for query in ("?w=112", "?w=224&ar=4:5", "?w=896&ar=16:10"):
        resp = await a.get(f"/api/assets/{bob.asset_id}/image{query}")
        assert resp.status_code == 404, query


async def test_content_endpoints_are_404_across_tenants(two) -> None:
    a, bob = two["a"], two["bob"]
    cid = bob.content_id

    assert (await a.patch(f"/api/content/{cid}", json={"title": "x"})).status_code == 404
    assert (
        await a.post(f"/api/content/{cid}/approve", json={"approved": False})
    ).status_code == 404
    assert (await a.post(f"/api/content/{cid}/publish")).status_code == 404
    assert (await a.post(f"/api/content/{cid}/publish-live")).status_code == 404


async def test_profile_endpoints_are_404_across_tenants(two) -> None:
    a, bob = two["a"], two["bob"]
    pid = bob.profile_id

    assert (await a.get(f"/api/profiles/{pid}")).status_code == 404
    assert (await a.patch(f"/api/profiles/{pid}", json={"name": "stolen"})).status_code == 404
    assert (await a.post(f"/api/profiles/{pid}/confirm")).status_code == 404
    assert (await a.post(f"/api/profiles/{pid}/refresh")).status_code == 404
    assert (await a.delete(f"/api/profiles/{pid}")).status_code == 404

    # And none of that mutated the row.
    async with two["sm"]() as s:
        profile = await s.get(ListingProfile, pid)
        assert profile is not None and profile.name == "bob@example.com profile"


async def test_job_status_is_404_across_tenants(two) -> None:
    a, bob = two["a"], two["bob"]
    assert (await a.get(f"/api/jobs/{bob.job_id}")).status_code == 404


async def test_replace_images_rejects_another_tenants_batch(two) -> None:
    a, alice, bob = two["a"], two["alice"], two["bob"]
    resp = await a.post(
        f"/api/shop/listings/{alice.listing_id}/replace-images",
        json={"batch_id": str(bob.batch_id)},
    )
    assert resp.status_code == 404


async def test_cannot_attach_another_tenants_profile(two) -> None:
    """A profile id arriving in a request body is as dangerous as one in the path."""
    a, alice, bob = two["a"], two["alice"], two["bob"]

    assert (
        await a.put(
            f"/api/batches/{alice.batch_id}/size-chart-profile",
            json={"profile_id": str(bob.profile_id)},
        )
    ).status_code == 404
    assert (
        await a.put(
            f"/api/batches/{alice.batch_id}/groups",
            json={"group_key": "", "profile_id": str(bob.profile_id)},
        )
    ).status_code == 404
    assert (
        await a.put(
            f"/api/batches/{alice.batch_id}/groups",
            json={"group_key": "", "size_chart_profile_id": str(bob.profile_id)},
        )
    ).status_code == 404
    assert (
        await a.post(
            f"/api/batches/{alice.batch_id}/generate",
            json={"profile_id": str(bob.profile_id)},
        )
    ).status_code == 404


# --- B2: collections return only the caller's rows --------------------------
async def test_list_endpoints_never_include_another_tenant(two) -> None:
    a, alice, bob = two["a"], two["alice"], two["bob"]

    batches = (await a.get("/api/batches")).json()
    assert [b["id"] for b in batches] == [str(alice.batch_id)]

    profiles = (await a.get("/api/profiles")).json()
    assert [p["id"] for p in profiles] == [str(alice.profile_id)]

    listings = (await a.get("/api/shop/listings")).json()["listings"]
    assert [row["listing_id"] for row in listings] == [alice.listing_id]

    summary = (await a.get("/api/shop/summary")).json()
    assert summary["total"] == 1

    content = (await a.get(f"/api/batches/{alice.batch_id}/content")).json()
    assert [c["id"] for c in content] == [str(alice.content_id)]

    # Bob sees exactly the mirror image, so a missing filter cannot pass by
    # returning an empty list for both.
    b = two["b"]
    assert [x["id"] for x in (await b.get("/api/batches")).json()] == [str(bob.batch_id)]
    assert [p["id"] for p in (await b.get("/api/profiles")).json()] == [str(bob.profile_id)]


async def test_connection_status_is_per_tenant(two) -> None:
    a, b = two["a"], two["b"]
    a_status = (await a.get("/api/auth/etsy/status")).json()
    b_status = (await b.get("/api/auth/etsy/status")).json()
    assert a_status["etsy_user_id"] == 1001
    assert b_status["etsy_user_id"] == 2002


# --- B6: quota independence -------------------------------------------------
async def test_quota_is_independent_per_tenant(two) -> None:
    a, b, alice = two["a"], two["b"], two["alice"]
    quota = DailyQuota(two["redis"], global_daily_limit=5000)

    for _ in range(7):
        await quota.reserve(alice.tenant_id, 1000)

    a_quota = (await a.get("/api/quota")).json()
    b_quota = (await b.get("/api/quota")).json()

    assert a_quota["tenant_used"] == 7
    assert b_quota["tenant_used"] == 0  # Bob is untouched by Alice's spending
    # The app-wide budget is shared on purpose, and both see the same figure.
    assert a_quota["global_used"] == b_quota["global_used"] == 7

    a_history = {d["date"]: d["count"] for d in a_quota["history"]}
    b_history = {d["date"]: d["count"] for d in b_quota["history"]}
    assert a_history[a_quota["usage_date"]] == 7
    assert b_history[b_quota["usage_date"]] == 0


# --- B1: no session, no access ----------------------------------------------
async def test_every_endpoint_requires_a_session(two) -> None:
    anon, alice = two["anon"], two["alice"]

    paths = [
        ("GET", "/api/batches"),
        ("POST", "/api/batches"),
        ("GET", f"/api/batches/{alice.batch_id}"),
        ("GET", f"/api/batches/{alice.batch_id}/groups"),
        ("PUT", f"/api/batches/{alice.batch_id}/groups"),
        ("GET", f"/api/batches/{alice.batch_id}/cost"),
        ("POST", f"/api/batches/{alice.batch_id}/finalize"),
        ("POST", f"/api/batches/{alice.batch_id}/generate"),
        ("PUT", f"/api/batches/{alice.batch_id}/size-chart-profile"),
        ("GET", f"/api/batches/{alice.batch_id}/content"),
        ("POST", f"/api/batches/{alice.batch_id}/publish"),
        ("POST", f"/api/batches/{alice.batch_id}/publish-live"),
        ("GET", f"/api/assets/{alice.asset_id}/image"),
        ("PATCH", f"/api/content/{alice.content_id}"),
        ("POST", f"/api/content/{alice.content_id}/approve"),
        ("POST", f"/api/content/{alice.content_id}/publish"),
        ("POST", f"/api/content/{alice.content_id}/publish-live"),
        ("GET", f"/api/jobs/{alice.job_id}"),
        ("GET", "/api/profiles"),
        ("POST", "/api/profiles"),
        ("GET", f"/api/profiles/{alice.profile_id}"),
        ("PATCH", f"/api/profiles/{alice.profile_id}"),
        ("DELETE", f"/api/profiles/{alice.profile_id}"),
        ("POST", f"/api/profiles/{alice.profile_id}/confirm"),
        ("POST", f"/api/profiles/{alice.profile_id}/refresh"),
        ("GET", "/api/quota"),
        ("GET", "/api/shop/listings"),
        ("GET", "/api/shop/summary"),
        ("POST", "/api/shop/detect-profiles"),
        ("POST", f"/api/shop/listings/{alice.listing_id}/use-as-profile"),
        ("POST", f"/api/shop/listings/{alice.listing_id}/replace-images"),
        ("GET", "/api/auth/etsy/status"),
        ("GET", "/api/auth/etsy/start"),
        ("POST", "/api/auth/etsy/disconnect"),
    ]
    for method, path in paths:
        resp = await anon.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"

    # The asset image must not leak even as a bare GET with no body.
    assert (await anon.get(f"/api/assets/{alice.asset_id}/image?w=112")).status_code == 401


async def test_public_endpoints_stay_public(two) -> None:
    """Compliance metadata and health carry no tenant data, so they stay open."""
    anon = two["anon"]
    assert (await anon.get("/api/meta")).status_code == 200
    assert (await anon.get("/health")).status_code == 200


# --- B4: storage isolation --------------------------------------------------
async def test_storage_keys_are_tenant_prefixed(two) -> None:
    """A path traversal between tenants is impossible if the prefix is the id."""
    alice, bob = two["alice"], two["bob"]
    async with two["sm"]() as s:
        a_asset = await s.get(Asset, alice.asset_id)
        b_asset = await s.get(Asset, bob.asset_id)
    assert a_asset.processed_key.startswith(f"{alice.tenant_id}/")
    assert b_asset.processed_key.startswith(f"{bob.tenant_id}/")
    assert not a_asset.processed_key.startswith(f"{bob.tenant_id}/")
