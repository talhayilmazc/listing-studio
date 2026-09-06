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
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.sm = sm  # type: ignore[attr-defined]  (tests reach into the DB directly)
        yield ac

    await engine.dispose()


async def _tenant_id(client: AsyncClient) -> uuid.UUID:
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.execute(select(Tenant).where(Tenant.email == "dev@localhost"))
        return row.scalar_one().id


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
    # No LLM_API_KEY configured in the test settings -> 503 (checked before the
    # profile lookup, so any profile_id in the required body is fine here).
    res = await client.post(
        f"/api/batches/{batch_id}/generate", json={"profile_id": str(uuid.uuid4())}
    )
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


async def test_current_tenant_is_idempotent_on_conflict(client: AsyncClient, monkeypatch) -> None:
    """The insert path tolerates a concurrent creation (IntegrityError -> refetch)."""
    # Ensure the dev tenant already exists (a prior request created it).
    await client.get("/api/quota")

    # Force the initial lookup to miss so the insert runs and hits the unique
    # email constraint -- exactly the concurrent-request race.
    real = deps._fetch_dev_tenant
    seen = {"n": 0}

    async def flaky(session):  # noqa: ANN001, ANN202
        seen["n"] += 1
        if seen["n"] == 1:
            return None
        return await real(session)

    monkeypatch.setattr(deps, "_fetch_dev_tenant", flaky)

    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = await deps.current_tenant(session=s)
    assert tenant.email == "dev@localhost"

    # Still exactly one dev tenant.
    async with client.sm() as s:  # type: ignore[attr-defined]
        rows = await s.execute(select(Tenant).where(Tenant.email == "dev@localhost"))
        assert len(rows.scalars().all()) == 1


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
    assert "cache-control" not in {k.lower() for k in after.headers}


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
    assert "cache-control" not in {k.lower() for k in full.headers}


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
