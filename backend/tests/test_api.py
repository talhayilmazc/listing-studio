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
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(fake_redis, global_daily_limit=9000)

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
    assert quota["global_remaining"] == 9000

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
    # No LLM_API_KEY configured in the test settings -> 503.
    res = await client.post(f"/api/batches/{batch_id}/generate")
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
            title="A perfectly fine title",
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
    await client.patch(f"/api/content/{content_id}", json={"title": "A good title"})
    ok = await client.post(f"/api/content/{content_id}/approve", json={"approved": True})
    assert ok.status_code == 200
    assert ok.json()["content"]["approved"] is True


async def test_batch_cost_from_tokens(client: AsyncClient) -> None:
    batch_id, _ = await _seed_content(client, [f"tag{i}" for i in range(13)])
    cost = (await client.get(f"/api/batches/{batch_id}/cost")).json()
    assert cost["listing_count"] == 1
    assert cost["total_input_tokens"] == 300
    assert cost["total_output_tokens"] == 120
    # 300/1e6*$1 + 120/1e6*$5 = 0.0009
    assert float(cost["total_cost_usd"]) == pytest.approx(0.0009, rel=1e-6)
