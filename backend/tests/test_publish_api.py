"""Publish endpoint tests: enqueue + validation + job status (no real calls)."""

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
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
from app.core.sessions import SessionStore
from app.main import create_app
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session
from tests.support import VALID_TITLE

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

    # Seed the dev tenant + an active connection.
    async with sm() as s:
        tenant = Tenant(email=OWNER_EMAIL, password_hash="!", daily_quota=2000)
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
            "redis": fake_redis,
            "app": app,
        }
    await engine.dispose()


async def _shop(s, tenant_id) -> uuid.UUID:
    rows = await s.execute(select(EtsyConnection.id).where(EtsyConnection.tenant_id == tenant_id))
    return rows.scalars().first()


async def _add_content(
    sm, tenant_id, *, approved=True, valid=True, listing_id=None, listing_state=None, profile=True
) -> uuid.UUID:
    """A listing written with a fresh profile of the tenant's shop (so it has a shop
    to go to); ``listing_id`` also records its draft there (v5 §E)."""
    from datetime import datetime, timezone

    from app.db.models import ListingProfile, ListingPublication

    async with sm() as s:
        shop = await _shop(s, tenant_id)
        profile_row = None
        if profile:
            profile_row = ListingProfile(
                tenant_id=tenant_id,
                connection_id=shop,
                name="Standard Tee",
                reference_listing_id=555,
                content_template="digital_products",
                confirmed=True,
                cached_payload={"price": 25.0, "images": [], "description": "Ref\n\nBody"},
                updated_at=datetime.now(timezone.utc),
            )
            s.add(profile_row)
            await s.flush()
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
            listing_profile_id=profile_row.id if profile_row else None,
        )
        s.add(content)
        await s.flush()
        if listing_id is not None:
            s.add(
                ListingPublication(
                    tenant_id=tenant_id,
                    content_id=content.id,
                    connection_id=shop,
                    profile_id=profile_row.id if profile_row else None,
                    etsy_listing_id=listing_id,
                    state=listing_state or "draft",
                )
            )
        await s.commit()
        return content.id


async def test_publish_one_enqueues_job(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    res = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert res.status_code == 200
    # One job per shop; by default the listing goes to its own profile's shop.
    [body] = res.json()["jobs"]
    assert body["content_id"] == str(content_id)

    # A create_draft job was created and enqueued.
    assert ctx["enqueuer"].calls and ctx["enqueuer"].calls[0][0] == "run_publish_job"
    async with ctx["sm"]() as s:
        job = await s.get(Job, uuid.UUID(body["job_id"]))
        assert job.type.value == "create_draft"
        assert job.payload["content_id"] == str(content_id)


async def test_asking_again_reuses_the_unfinished_job(ctx) -> None:
    """A job paused until the daily reset may wait for hours. Asking again must not
    queue a second one, which would create a second draft when both run."""
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    first = (await ctx["client"].post(f"/api/content/{content_id}/publish")).json()
    second = (await ctx["client"].post(f"/api/content/{content_id}/publish")).json()

    assert first["jobs"][0]["job_id"] == second["jobs"][0]["job_id"]
    assert len(ctx["enqueuer"].calls) == 1
    async with ctx["sm"]() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
    assert len(jobs) == 1


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
    assert "already has a draft in this shop" in res.json()["detail"]


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


# --- reference freshness (CLAUDE.md: Etsy data older than 24h is not used) --
async def _attach_profile(sm, content_id, *, age_hours: float, payload=True) -> None:
    from datetime import datetime, timedelta, timezone

    from app.db.models import ListingProfile

    async with sm() as s:
        content = await s.get(GeneratedContent, content_id)
        profile = ListingProfile(
            tenant_id=content.tenant_id,
            connection_id=await _shop(s, content.tenant_id),
            confirmed=True,
            name="Standard Tee",
            reference_listing_id=555,
            # The seeded content is a digital print; an apparel template would
            # (rightly) reject its title under the apparel content policy.
            content_template="digital_products",
            cached_payload={"price": 25.0, "images": [], "description": "Ref"} if payload else None,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        )
        s.add(profile)
        await s.flush()
        content.listing_profile_id = profile.id
        await s.commit()


async def test_publish_refuses_a_reference_older_than_a_day(ctx) -> None:
    cid = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _attach_profile(ctx["sm"], cid, age_hours=25)

    res = await ctx["client"].post(f"/api/content/{cid}/publish")
    assert res.status_code == 409
    assert "more than a day old" in res.json()["detail"]
    assert "Standard Tee" in res.json()["detail"]
    assert ctx["enqueuer"].calls == []  # nothing built from expired Etsy data


async def test_publish_refuses_a_reference_the_retention_job_cleared(ctx) -> None:
    cid = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _attach_profile(ctx["sm"], cid, age_hours=1, payload=False)
    assert (await ctx["client"].post(f"/api/content/{cid}/publish")).status_code == 409


async def test_publish_accepts_a_fresh_reference(ctx) -> None:
    cid = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _attach_profile(ctx["sm"], cid, age_hours=23)
    res = await ctx["client"].post(f"/api/content/{cid}/publish")
    assert res.status_code == 200, res.json()
    assert len(ctx["enqueuer"].calls) == 1


async def test_batch_publish_skips_expired_references_with_the_reason(ctx) -> None:
    fresh = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _attach_profile(ctx["sm"], fresh, age_hours=2)
    async with ctx["sm"]() as s:
        batch_id = (await s.get(GeneratedContent, fresh)).batch_id
        stale_asset = Asset(
            batch_id=batch_id,
            tenant_id=ctx["tenant_id"],
            original_filename="old_BR1.png",
            storage_key="k2",
            status=AssetStatus.processed,
            rank=2,
        )
        s.add(stale_asset)
        await s.flush()
        stale_content = GeneratedContent(
            tenant_id=ctx["tenant_id"],
            batch_id=batch_id,
            asset_id=stale_asset.id,
            title=VALID_TITLE,
            tags=[f"tag{i}" for i in range(13)],
            description="d",
            approved=True,
        )
        s.add(stale_content)
        await s.commit()
        stale = stale_content.id
    await _attach_profile(ctx["sm"], stale, age_hours=30)

    body = (await ctx["client"].post(f"/api/batches/{batch_id}/publish")).json()
    assert [j["content_id"] for j in body["jobs"]] == [str(fresh)]
    assert body["skipped"][0]["content_id"] == str(stale)
    assert "more than a day old" in body["skipped"][0]["reason"]


# --- settings the API cannot make (Etsy's Creativity Standards question) -------------
async def _batch_of(ctx, content_id) -> uuid.UUID:
    async with ctx["sm"]() as s:
        return (await s.get(GeneratedContent, content_id)).batch_id


async def test_a_draft_lists_what_still_has_to_be_set_in_shop_manager(ctx) -> None:
    draft = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    rows = (await ctx["client"].get(f"/api/batches/{await _batch_of(ctx, draft)}/content")).json()
    [pub] = rows[0]["publications"]
    assert pub["state"] == "draft"
    assert pub["listing_link"].endswith("/listing-editor/edit/777")  # where to set it
    assert [step["label"] for step in pub["manual_steps"]] == ["How does your shop produce this item?"]
    assert "Shop Manager" in pub["manual_steps"][0]["detail"]


async def test_a_live_listing_has_no_steps_left(ctx) -> None:
    live = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=888, listing_state="active")
    rows = (await ctx["client"].get(f"/api/batches/{await _batch_of(ctx, live)}/content")).json()
    assert rows[0]["publications"][0]["manual_steps"] == []


async def test_the_finished_draft_job_carries_the_steps(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    job_id = await _job_for(ctx, content_id)
    body = (await ctx["client"].get(f"/api/jobs/{job_id}")).json()
    assert [s["key"] for s in body["manual_steps"]] == ["creativity_production"]


async def test_the_list_is_data_driven(ctx, monkeypatch) -> None:
    """A new setting Etsy keeps out of the API is one entry in the registry."""
    from app.etsy import manual_fields
    from app.etsy.manual_fields import ManualField

    monkeypatch.setattr(
        manual_fields,
        "MANUAL_FIELDS",
        (
            *manual_fields.MANUAL_FIELDS,
            ManualField("apparel_only", "Apparel setting", "Set it by hand.", frozenset({"apparel"})),
            ManualField("digital_only", "Digital setting", "Set it by hand.", frozenset({"digital_products"})),
        ),
    )
    draft = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)  # a digital_products profile
    rows = (await ctx["client"].get(f"/api/batches/{await _batch_of(ctx, draft)}/content")).json()
    keys = [s["key"] for s in rows[0]["publications"][0]["manual_steps"]]
    assert keys == ["creativity_production", "digital_only"]
