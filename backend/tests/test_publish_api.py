"""Publish endpoint tests: enqueue + validation + job status (no real calls)."""

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.crypto import TokenCipher
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobType,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.connection import ConnectionService
from app.main import create_app
from tests.support import VALID_TITLE

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

    # Seed the dev tenant + an active connection.
    async with sm() as s:
        tenant = Tenant(email=DEV_EMAIL, password_hash="!", daily_quota=2000)
        s.add(tenant)
        await s.flush()
        s.add(
            EtsyConnection(
                tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900, shop_id=900
            )
        )
        await s.commit()
        tenant_id = tenant.id

    enqueuer = StubEnqueuer()

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[deps.get_connection_service] = lambda: ConnectionService(
        TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield {"client": ac, "sm": sm, "enqueuer": enqueuer, "tenant_id": tenant_id}
    await engine.dispose()


async def _add_content(
    sm, tenant_id, *, approved=True, valid=True, listing_id=None, listing_state=None
) -> uuid.UUID:
    async with sm() as s:
        batch = UploadBatch(tenant_id=tenant_id, status=UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()
        asset = Asset(
            batch_id=batch.id,
            tenant_id=tenant_id,
            original_filename="tasarim_BR5475.png",
            parsed_sku="BR5475",
            storage_key="k",
            status=AssetStatus.processed,
            rank=1,
        )
        s.add(asset)
        await s.flush()
        content = GeneratedContent(
            tenant_id=tenant_id,
            batch_id=batch.id,
            asset_id=asset.id,
            title=VALID_TITLE if valid else "too short",
            tags=[f"tag{i}" for i in range(13)],
            description="Fixed description.",
            approved=approved,
            etsy_listing_id=listing_id,
            etsy_listing_state=listing_state,
        )
        s.add(content)
        await s.commit()
        return content.id


async def test_publish_one_enqueues_job(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 200
    body = res.json()
    assert body["content_id"] == str(content_id)

    # A create_draft job was created and enqueued.
    assert ctx["enqueuer"].calls and ctx["enqueuer"].calls[0][0] == "run_publish_job"
    async with ctx["sm"]() as s:
        job = await s.get(Job, uuid.UUID(body["job_id"]))
        assert job.type.value == "create_draft"
        assert job.payload["content_id"] == str(content_id)


async def test_publish_one_rejects_unapproved(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=False)
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 409
    assert not ctx["enqueuer"].calls


async def test_publish_one_rejects_invalid(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], valid=False)
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 409


async def test_publish_one_rejects_already_published(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=42)
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 409
    assert "already published" in res.json()["detail"]


async def test_publish_requires_connection(ctx) -> None:
    # Revoke the connection so there is none active.
    async with ctx["sm"]() as s:
        rows = await s.execute(select(EtsyConnection))
        conn = rows.scalars().first()
        conn.status = ConnectionStatus.revoked
        await s.commit()
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 409
    assert "connect" in res.json()["detail"].lower()


async def test_batch_publish_skips_and_enqueues(ctx) -> None:
    good = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, valid=True)
    await _add_content(ctx["sm"], ctx["tenant_id"], approved=False)  # skipped silently
    bad = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, valid=False)
    # all three share different batches; publish each batch of the good one
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, good)
        batch_id = content.batch_id
    res = await ctx["client"].post(f"/api/batches/{batch_id}/publish")
    assert res.status_code == 200
    body = res.json()
    assert len(body["jobs"]) == 1 and body["jobs"][0]["content_id"] == str(good)


async def _job_for(ctx, content_id) -> uuid.UUID:
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        job = Job(
            tenant_id=ctx["tenant_id"],
            connection_id=(await s.execute(select(EtsyConnection))).scalars().first().id,
            type=JobType.create_draft,
            payload={"content_id": str(content_id)},
            batch_id=content.batch_id,
        )
        s.add(job)
        await s.commit()
        return job.id


async def test_job_status_links_draft_to_shop_manager(ctx) -> None:
    # A4: a draft (no active state) links to the editable Shop Manager page.
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    job_id = await _job_for(ctx, content_id)
    body = (await ctx["client"].get(f"/api/jobs/{job_id}")).json()
    assert body["listing_id"] == 777
    assert body["is_draft"] is True
    assert body["listing_url"] == "https://www.etsy.com/your/shops/me/listing-editor/edit/777"


async def test_job_status_links_active_to_public_url(ctx) -> None:
    content_id = await _add_content(
        ctx["sm"], ctx["tenant_id"], listing_id=888, listing_state="active"
    )
    job_id = await _job_for(ctx, content_id)
    body = (await ctx["client"].get(f"/api/jobs/{job_id}")).json()
    assert body["is_draft"] is False
    assert body["listing_url"] == "https://www.etsy.com/listing/888"


# --- Publish now (draft -> active), spec §E ---------------------------------
async def test_publish_live_enqueues_for_approved_draft(ctx) -> None:
    cid = await _add_content(
        ctx["sm"], ctx["tenant_id"], approved=True, listing_id=777, listing_state="draft"
    )
    res = await ctx["client"].post(f"/api/content/{cid}/publish-live")
    assert res.status_code == 200
    assert any(call[0] == "run_publish_live_job" for call in ctx["enqueuer"].calls)


async def test_publish_live_requires_a_created_draft(ctx) -> None:
    cid = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True)  # no draft yet
    res = await ctx["client"].post(f"/api/content/{cid}/publish-live")
    assert res.status_code == 409 and "draft" in res.json()["detail"]


async def test_publish_live_requires_approval(ctx) -> None:
    cid = await _add_content(
        ctx["sm"], ctx["tenant_id"], approved=False, listing_id=1, listing_state="draft"
    )
    res = await ctx["client"].post(f"/api/content/{cid}/publish-live")
    assert res.status_code == 409 and res.json()["detail"] == "not approved"


async def test_publish_live_rejects_already_active(ctx) -> None:
    cid = await _add_content(
        ctx["sm"], ctx["tenant_id"], approved=True, listing_id=2, listing_state="active"
    )
    res = await ctx["client"].post(f"/api/content/{cid}/publish-live")
    assert res.status_code == 409 and res.json()["detail"] == "already published"


async def test_replace_images_enqueues_job(ctx) -> None:
    async with ctx["sm"]() as s:
        batch = UploadBatch(
            tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=2
        )
        s.add(batch)
        await s.commit()
        batch_id = batch.id
    res = await ctx["client"].post(
        "/api/shop/listings/12345/replace-images", json={"batch_id": str(batch_id)}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["listing_id"] == 12345
    assert any(c[0] == "run_replace_images_job" for c in ctx["enqueuer"].calls)


async def test_publish_all_live_publishes_approved_draft(ctx) -> None:
    good = await _add_content(
        ctx["sm"], ctx["tenant_id"], approved=True, listing_id=10, listing_state="draft"
    )
    async with ctx["sm"]() as s:
        batch_id = (await s.get(GeneratedContent, good)).batch_id
    res = await ctx["client"].post(f"/api/batches/{batch_id}/publish-live")
    assert res.status_code == 200
    assert [j["content_id"] for j in res.json()["jobs"]] == [str(good)]
