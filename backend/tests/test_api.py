"""API endpoint tests: upload flow, review, cost, quota, meta.

Runs the ASGI app in-process with dependency overrides (in-memory SQLite, temp
local storage, fake Redis). No real LLM or Etsy calls.
"""

import io
import uuid
from collections.abc import AsyncIterator
from typing import Callable

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import GeneratedContent, Tenant
from app.core.sessions import SessionStore
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from tests.support import VALID_TITLE


@pytest_asyncio.fixture()
async def client(tmp_path) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    ingestor = BatchIngestor(
        sessionmaker=sm,
        storage=storage,
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
    )
    fake_redis = FakeAsyncRedis()

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.dependency_overrides[deps.get_ingestor] = lambda: ingestor
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(fake_redis, global_daily_limit=5000)
    app.dependency_overrides[deps.get_redis] = lambda: fake_redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(fake_redis)

    tenant_id = await make_tenant(sm, "owner@example.com")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as ac:
        ac.sm = sm  # type: ignore[attr-defined]  (tests reach into the DB directly)
        ac.tenant_id = tenant_id  # type: ignore[attr-defined]
        ac.redis = fake_redis  # type: ignore[attr-defined]
        ac.app = app  # type: ignore[attr-defined]
        authenticate(ac, await open_session(fake_redis, tenant_id))
        yield ac

    await engine.dispose()


async def _tenant_id(client: AsyncClient) -> uuid.UUID:
    return client.tenant_id  # type: ignore[attr-defined]


async def test_upload_flow(client: AsyncClient, make_image: Callable[..., bytes]) -> None:
    created = await client.post("/api/batches")
    assert created.status_code == 201
    batch_id = created.json()["id"]

    png = make_image(400, 300)
    up = await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": ("SKU1_front.png", io.BytesIO(png), "image/png")},
    )
    assert up.status_code == 201
    asset = up.json()
    assert asset["parsed_sku"] == "SKU1"
    assert asset["status"] == "processed"
    assert asset["rank"] == 1

    fin = await client.post(f"/api/batches/{batch_id}/finalize")
    assert fin.json()["status"] == "ready"

    detail = await client.get(f"/api/batches/{batch_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["asset_count"] == 1
    assert body["assets"][0]["has_content"] is False

    img = await client.get(f"/api/assets/{asset['id']}/image")
    assert img.status_code == 200
    assert img.headers["content-type"].startswith("image/")
    from PIL import Image

    assert Image.open(io.BytesIO(img.content)).format in {"JPEG", "PNG"}


async def test_quota_and_meta(client: AsyncClient) -> None:
    quota = (await client.get("/api/quota")).json()
    assert quota["tenant_limit"] == 2000
    assert quota["tenant_remaining"] == 2000
    assert quota["global_remaining"] == 5000

    meta = (await client.get("/api/meta")).json()
    assert meta["support_email"]
    assert meta["trademark_notice"] == (
        "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the "
        "Etsy API but is not endorsed or certified by Etsy, Inc."
    )


async def test_generate_requires_llm_key(
    client: AsyncClient, make_image: Callable[..., bytes]
) -> None:
    batch_id = (await client.post("/api/batches")).json()["id"]
    await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": ("SKU1.png", io.BytesIO(make_image(100, 100)), "image/png")},
    )
    # Ownership is settled before anything else, so an unknown profile is 404
    # even though the service is unconfigured (production-spec B3).
    unknown = await client.post(
        f"/api/batches/{batch_id}/generate", json={"profile_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404

    # With nothing to reject on ownership grounds, the missing key surfaces.
    res = await client.post(f"/api/batches/{batch_id}/generate", json={})
    assert res.status_code == 503


async def _seed_content(client: AsyncClient, tags: list[str]) -> tuple[str, str]:
    """Create a batch+asset via API, then insert a GeneratedContent row directly."""
    batch_id = (await client.post("/api/batches")).json()["id"]
    asset = (
        await client.post(
            f"/api/batches/{batch_id}/assets",
            files={"file": ("SKU9_front.png", io.BytesIO(_png()), "image/png")},
        )
    ).json()
    tenant_id = await _tenant_id(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = GeneratedContent(
            tenant_id=tenant_id,
            batch_id=uuid.UUID(batch_id),
            asset_id=uuid.UUID(asset["id"]),
            title=VALID_TITLE,
            tags=tags,
            description="A description.",
            model_used="claude-haiku-4-5-20251001",
            input_tokens=300,
            output_tokens=120,
            approved=False,
        )
        s.add(content)
        await s.commit()
        await s.refresh(content)
        return batch_id, str(content.id)


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (80, 80), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


async def test_review_edit_and_approve(client: AsyncClient) -> None:
    good_tags = [f"tag{i}" for i in range(13)]
    batch_id, content_id = await _seed_content(client, good_tags)

    listing = (await client.get(f"/api/batches/{batch_id}/content")).json()
    assert len(listing) == 1
    assert listing[0]["original_filename"] == "SKU9_front.png"

    # Edit into an invalid state (title too long) -> validation flags it.
    bad = await client.patch(f"/api/content/{content_id}", json={"title": "x" * 141})
    assert bad.json()["validation"]["valid"] is False

    # Approving invalid content is rejected.
    denied = await client.post(f"/api/content/{content_id}/approve", json={"approved": True})
    assert denied.status_code == 422

    # Fix the title, then approve succeeds.
    await client.patch(f"/api/content/{content_id}", json={"title": VALID_TITLE})
    ok = await client.post(f"/api/content/{content_id}/approve", json={"approved": True})
    assert ok.status_code == 200
    assert ok.json()["content"]["approved"] is True


async def test_no_session_is_rejected_everywhere(client: AsyncClient) -> None:
    """There is no default tenant: without a session the API answers 401 (B1).

    The dev get-or-create tenant is gone, so a request that carries no cookie has
    no identity to fall back to and must be refused before any handler runs.
    """
    from app.core.sessions import SESSION_COOKIE

    client.cookies.delete(SESSION_COOKIE)

    for method, path in [
        ("GET", "/api/batches"),
        ("POST", "/api/batches"),
        ("GET", "/api/quota"),
        ("GET", "/api/profiles"),
        ("GET", "/api/shop/summary"),
        ("GET", "/api/auth/etsy/status"),
    ]:
        resp = await client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"

    # /meta and /health carry no tenant data and stay open.
    assert (await client.get("/api/meta")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_stale_session_cookie_is_rejected(client: AsyncClient) -> None:
    """A token with no Redis record must not resolve to anyone."""
    from app.core.sessions import SESSION_COOKIE

    client.cookies.set(SESSION_COOKIE, "not-a-real-token")
    assert (await client.get("/api/batches")).status_code == 401


async def test_deleted_tenant_invalidates_its_session(client: AsyncClient) -> None:
    """A live session whose tenant has gone is refused, not resurrected."""
    from sqlalchemy import delete

    assert (await client.get("/api/batches")).status_code == 200
    async with client.sm() as s:  # type: ignore[attr-defined]
        await s.execute(delete(Tenant).where(Tenant.id == client.tenant_id))  # type: ignore[attr-defined]
        await s.commit()
    assert (await client.get("/api/batches")).status_code == 401


async def test_batch_cost_from_tokens(client: AsyncClient) -> None:
    batch_id, _ = await _seed_content(client, [f"tag{i}" for i in range(13)])
    cost = (await client.get(f"/api/batches/{batch_id}/cost")).json()
    assert cost["listing_count"] == 1
    assert cost["total_input_tokens"] == 300
    assert cost["total_output_tokens"] == 120
    # 300/1e6*$1 + 120/1e6*$5 = 0.0009
    assert float(cost["total_cost_usd"]) == pytest.approx(0.0009, rel=1e-6)


async def test_asset_image_preview_widths(
    client: AsyncClient, make_image: "Callable[..., bytes]"
) -> None:
    """?w= returns a resized JPEG; the no-parameter response stays byte-identical."""
    from PIL import Image

    created = await client.post("/api/batches")
    batch_id = created.json()["id"]
    up = await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": ("SKU9_front.png", io.BytesIO(make_image(800, 600)), "image/png")},
    )
    asset_id = up.json()["id"]

    # Baseline: the parameterless response, captured before any preview exists.
    full = await client.get(f"/api/assets/{asset_id}/image")
    assert full.status_code == 200
    baseline = full.content

    preview = await client.get(f"/api/assets/{asset_id}/image", params={"w": 112})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(preview.content))
    assert im.format == "JPEG"
    assert im.width == 112
    assert len(preview.content) < len(baseline)

    # Cached: the second request returns the same bytes without re-encoding.
    again = await client.get(f"/api/assets/{asset_id}/image", params={"w": 112})
    assert again.content == preview.content

    # The parameterless path is unaffected by the preview having been generated.
    after = await client.get(f"/api/assets/{asset_id}/image")
    assert after.status_code == 200
    assert after.content == baseline
    assert after.headers["content-type"] == full.headers["content-type"]
    # The preview's browser-cache policy does not leak onto the original, which
    # keeps the hardening default: tenant designs are never stored by caches.
    assert after.headers["cache-control"] == "no-store"


async def test_asset_image_preview_width_allowlist(
    client: AsyncClient, make_image: "Callable[..., bytes]"
) -> None:
    """An off-allowlist width is rejected, so ?w= cannot drive arbitrary resizes."""
    created = await client.post("/api/batches")
    batch_id = created.json()["id"]
    up = await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": ("SKU8_front.png", io.BytesIO(make_image(400, 300)), "image/png")},
    )
    asset_id = up.json()["id"]

    for bad in (113, 4000, 0, -1):
        resp = await client.get(f"/api/assets/{asset_id}/image", params={"w": bad})
        assert resp.status_code == 400, bad

    for good in (112, 224, 448):
        resp = await client.get(f"/api/assets/{asset_id}/image", params={"w": good})
        assert resp.status_code == 200, good


async def test_asset_image_aspect_variant(
    client: AsyncClient, make_image: "Callable[..., bytes]"
) -> None:
    """?ar= crops to a tile ratio; it is allowlisted and requires w."""
    from PIL import Image

    created = await client.post("/api/batches")
    batch_id = created.json()["id"]
    up = await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": ("SKU7_front.png", io.BytesIO(make_image(900, 1200)), "image/png")},
    )
    asset_id = up.json()["id"]

    tile = await client.get(f"/api/assets/{asset_id}/image", params={"w": 224, "ar": "4:5"})
    assert tile.status_code == 200
    im = Image.open(io.BytesIO(tile.content))
    assert abs(im.width / im.height - 4 / 5) < 0.01

    hero = await client.get(f"/api/assets/{asset_id}/image", params={"w": 448, "ar": "16:10"})
    assert hero.status_code == 200
    assert abs(Image.open(io.BytesIO(hero.content)).width / Image.open(io.BytesIO(hero.content)).height - 16 / 10) < 0.01

    # Each ratio is cached separately from the plain width variant.
    again = await client.get(f"/api/assets/{asset_id}/image", params={"w": 224, "ar": "4:5"})
    assert again.content == tile.content
    plain = await client.get(f"/api/assets/{asset_id}/image", params={"w": 224})
    assert plain.status_code == 200
    assert plain.content != tile.content

    for bad in ("1:1", "3:2", "banana"):
        r = await client.get(f"/api/assets/{asset_id}/image", params={"w": 224, "ar": bad})
        assert r.status_code == 400, bad

    # ar without w would silently do nothing, so it is rejected.
    assert (await client.get(f"/api/assets/{asset_id}/image", params={"ar": "4:5"})).status_code == 400

    # And the parameterless response is still the untouched original.
    full = await client.get(f"/api/assets/{asset_id}/image")
    assert full.status_code == 200
    assert full.headers["cache-control"] == "no-store"


async def test_quota_history_series(client: AsyncClient) -> None:
    """History is 7 days oldest-first, gaps zero-filled, today from the live counter."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import ApiUsage

    today = datetime.now(timezone.utc).date()
    first = (await client.get("/api/quota")).json()  # also creates the dev tenant
    assert len(first["history"]) == 7  # present with no api_usage rows at all
    assert all(h["count"] == 0 for h in first["history"])

    tenant_id = await _tenant_id(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        # Two days inside the window, one deliberately outside it.
        s.add(ApiUsage(tenant_id=tenant_id, usage_date=today - timedelta(days=2), request_count=41))
        s.add(ApiUsage(tenant_id=tenant_id, usage_date=today - timedelta(days=5), request_count=7))
        s.add(ApiUsage(tenant_id=tenant_id, usage_date=today - timedelta(days=9), request_count=999))
        await s.commit()

    body = (await client.get("/api/quota")).json()
    history = body["history"]

    assert len(history) == 7
    dates = [h["date"] for h in history]
    assert dates == sorted(dates)  # oldest first
    assert dates[-1] == today.isoformat()
    assert dates[0] == (today - timedelta(days=6)).isoformat()

    by_date = {h["date"]: h["count"] for h in history}
    assert by_date[(today - timedelta(days=2)).isoformat()] == 41
    assert by_date[(today - timedelta(days=5)).isoformat()] == 7
    assert by_date[(today - timedelta(days=1)).isoformat()] == 0  # gap zero-filled
    assert 999 not in by_date.values()  # outside the window
    # Today mirrors the live counter the same response reports.
    assert by_date[today.isoformat()] == body["tenant_used"]

    # The pre-existing fields are untouched.
    assert body["tenant_limit"] == 2000
    assert body["global_remaining"] == 5000
