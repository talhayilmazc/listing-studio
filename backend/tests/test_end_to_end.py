"""Part B end to end (B10), as a seller with one shop and as a seller with 15 shops in 6 groups.

The real API (in process) and the real draft and go-live workers, with Etsy faked at the HTTP
level (every shop, no faults: the faults have their own test, tests/test_publish_volume.py):

* Where and when: a one-shop seller sees the shop's name; the 15-shop seller sends a batch to
  two overlapping groups and every listing becomes a draft in every shop of both, each once;
* the drafts are made by ``run_publish_job`` and exist on Etsy exactly once each;
* spread out over the shops' days: no design live in two shops in the same minute, a shop's
  daily limit kept; then the Scheduled page moves, undoes, pauses and publishes one now, which
  ``run_publish_live_job`` takes live;
* a profile's Shops table lists every shop, personalization is set for a selection.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    ProfileShopLink,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy import api as etsy_api
from app.etsy import publisher
from app.etsy.connection import ConnectionService
from app.etsy.oauth import TokenResponse
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.main import create_app
from app.pipeline.storage import LocalStorage
from app.workers import plans
from app.workers import publish as publish_worker
from app.workers.schedule import release_scheduled_publishes
from tests.auth_support import BROWSER_HEADERS, authenticate, open_session
from tests.test_multi_shop import Queue
from tests.test_publish_volume import _png
from tests.test_publisher import REFERENCE

ZONE = "America/Chicago"
#: createDraftListing's request fields in Etsy's OpenAPI document (spec 3.0.0, fetched 2026-10-22). Etsy's
#: Creativity Standards questions "How does your shop produce this item?" and "What tools are used to make
#: this item?" are not among them, nor in getListing or updateListing (etsy/manual_fields.py).
CREATE_FIELDS = {
    "description", "ecgt_after_sales_service_info", "ecgt_garan_brand", "ecgt_garan_guarantee_details", "ecgt_garan_model",
    "ecgt_garan_years", "ecgt_other_commercial_guarantee_details", "ecgt_software_update_details", "image_ids",
    "is_customizable", "is_supply", "is_taxable", "item_dimensions_unit", "item_height", "item_length", "item_weight",
    "item_weight_unit", "item_width", "materials", "price", "processing_max", "processing_min", "production_partner_ids",
    "quantity", "readiness_state_id", "return_policy_id", "shipping_profile_id", "shop_section_id", "should_auto_renew",
    "styles", "tags", "taxonomy_id", "title", "type", "when_made", "who_made",
}
CREATIVITY = ["creativity_production", "creativity_tools"]


async def _creativity_checked(w: dict) -> None:
    """Every draft, in every shop, lists Etsy's two Creativity Standards questions for Shop Manager
    (the API has no field for either), and no draft request sent a field Etsy does not list."""
    assert w["etsy"].create_fields <= CREATE_FIELDS, w["etsy"].create_fields - CREATE_FIELDS
    assert {"who_made", "when_made", "is_supply"} <= w["etsy"].create_fields  # the Creativity fields that are in the API
    cards = (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()
    publications = [p for c in cards for p in c["publications"]]
    assert publications and len(publications) == len(w["etsy"].listings)
    for p in publications:
        assert [m["key"] for m in p["manual_steps"]] == CREATIVITY, p
    profile = next(x for x in (await w["client"].get("/api/profiles")).json() if x["id"] == str(w["profile"]))
    assert "How does your shop produce this item?" in profile["shop_manager_note"]
    assert "What tools are used to make this item?" in profile["shop_manager_note"]
TITLE = "Design {i:02d} Retro Graphic Tee, Vintage Sunset Shirt, Mountain Hiking Tee, Outdoor Adventure Shirt, Camping Lover Top"
TAGS = ["graphic tee", "retro shirt", "sunset tee", "hiking shirt", "camping gift", "outdoor tee", "mountain top",
        "nature lover", "adventure shirt", "vintage tee", "trail shirt", "forest tee", "summit gift"]


class FakeEtsy:
    """Every shop's side of the conversation; nothing fails."""

    def __init__(self) -> None:
        self.listings: dict[int, dict[str, Any]] = {}
        self._next = 70000
        #: Every field any createDraftListing carried (the Creativity Standards check below).
        self.create_fields: set[str] = set()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/v3/application")
        if m := re.fullmatch(r"/shops/(\d+)/sections", path):
            return httpx.Response(200, json={"results": []})
        if path.startswith("/seller-taxonomy/nodes/"):
            return httpx.Response(200, json={"results": []})
        if (m := re.fullmatch(r"/shops/(\d+)/listings", path)) and method == "GET":
            shop = int(m.group(1))
            drafts = [d for d in self.listings.values() if d["shop"] == shop]
            return httpx.Response(200, json={"count": len(drafts), "results": [
                {"listing_id": d["listing_id"], "title": d["title"], "state": d["state"], "original_creation_timestamp": d["created"]}
                for d in drafts]})
        if (m := re.fullmatch(r"/shops/(\d+)/listings", path)) and method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.create_fields |= set(form)
            self._next += 1
            self.listings[self._next] = {
                "listing_id": self._next, "shop": int(m.group(1)), "title": form["title"], "state": "draft",
                "created": int(time.time()), "taxonomy_id": int(form["taxonomy_id"]), "who_made": form["who_made"],
                "when_made": form["when_made"],
                "production_partners": [{"production_partner_id": int(p)} for p in form.get("production_partner_ids", "").split(",") if p],
                "images": [], "inventory": None,
            }
            return httpx.Response(201, json={"listing_id": self._next})
        if m := re.fullmatch(r"/listings/(\d+)", path):
            row = self.listings.get(int(m.group(1)))
            if row is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={k: row[k] for k in ("listing_id", "title", "state", "taxonomy_id", "who_made",
                                                                  "when_made", "production_partners")})
        if m := re.fullmatch(r"/listings/(\d+)/images", path):
            return httpx.Response(200, json={"results": [{"listing_image_id": i} for i in self.listings[int(m.group(1))]["images"]]})
        if m := re.fullmatch(r"/listings/(\d+)/inventory", path):
            row = self.listings[int(m.group(1))]
            if method == "GET":
                return httpx.Response(200, json=json.loads(row["inventory"]) if row["inventory"] else {"products": []})
            row["inventory"] = request.content
            return httpx.Response(200, json=json.loads(request.content))
        if m := re.fullmatch(r"/shops/(\d+)/listings/(\d+)/images", path):
            row = self.listings[int(m.group(2))]
            row["images"].append(len(row["images"]) + 1)
            return httpx.Response(201, json={"listing_image_id": len(row["images"]), "rank": len(row["images"])})
        if m := re.fullmatch(r"/shops/(\d+)/listings/(\d+)", path):
            row = self.listings[int(m.group(2))]
            text = request.content.decode()
            body = json.loads(text) if text.startswith("{") else {k: v[0] for k, v in parse_qs(text).items()}
            if body.get("state"):
                row["state"] = body["state"]
            return httpx.Response(200, json={"listing_id": row["listing_id"], "state": row["state"]})
        return httpx.Response(404, json={"error": f"no route for {method} {path}"})


class _Httpx:
    Timeout = httpx.Timeout

    def __init__(self, etsy: FakeEtsy) -> None:
        self._transport = httpx.MockTransport(etsy.handle)

    def AsyncClient(self, **kwargs: Any) -> httpx.AsyncClient:  # noqa: N802
        return httpx.AsyncClient(transport=self._transport, **kwargs)


async def _world(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch, *, shops: int, listings: int) -> dict:
    set_settings_override(test_settings.model_copy(update={
        "storage_dir": str(tmp_path / "storage"), "thumbnail_size": 300, "etsy_client_id": "k", "etsy_client_secret": "s",
        "max_shops_per_tenant": 20, "max_shops_app_wide": 50,
    }))
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    cipher = TokenCipher(Fernet.generate_key())
    storage = LocalStorage(tmp_path / "storage")
    async with sm() as s:
        tenant = Tenant(email="seller@example.com", password_hash="!", etsy_ceiling_override=20000, time_zone=ZONE)
        s.add(tenant)
        await s.flush()
        service = ConnectionService(cipher, client_id="k", token_url="t")
        connections = []
        for n in range(shops):
            shop = await service.save_from_tokens(
                s, tenant.id, TokenResponse(access_token=f"{500 + n}.abc", refresh_token="r", expires_in=3600),
                ["listings_r", "listings_w", "shops_r", "shops_w"])
            shop.shop_id, shop.shop_name, shop.position = 900 + n, f"Shop {n + 1:02d}", n
            connections.append(shop)
        await s.flush()
        profile = ListingProfile(
            tenant_id=tenant.id, connection_id=connections[0].id, name="Standard Tee", reference_listing_id=1,
            content_template="apparel", confirmed=True, listing_style="classic",
            cached_payload={**REFERENCE, "description": "Reference\n\nBody", "images": []}, updated_at=datetime.now(timezone.utc),
        )
        s.add(profile)
        await s.flush()
        for shop in connections[1:]:  # set up in every other shop, with that shop's own ids
            s.add(ProfileShopLink(tenant_id=tenant.id, profile_id=profile.id, connection_id=shop.id, status="ready",
                                  shipping_profile_id=155, return_policy_id=188, readiness_state_id=142, production_partner_ids=[17]))
        batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=listings)
        s.add(batch)
        await s.flush()
        contents = []
        for i in range(listings):
            key = f"p/{i:02d}.png"
            storage.put(key, _png(i * 11))
            asset = Asset(batch_id=batch.id, tenant_id=tenant.id, original_filename=f"D{i:02d}/front.png", parsed_sku=f"SKU{i:02d}",
                          storage_key=key, processed_key=key, status=AssetStatus.processed, rank=1, group_key=f"D{i:02d}")
            s.add(asset)
            await s.flush()
            content = GeneratedContent(tenant_id=tenant.id, batch_id=batch.id, asset_id=asset.id, approved=True,
                                       listing_profile_id=profile.id, title=TITLE.format(i=i), tags=TAGS,
                                       description=TITLE.format(i=i) + " Body.")
            s.add(content)
            await s.flush()
            contents.append(content.id)
        await s.commit()
        ids = {"tenant": tenant.id, "shops": [c.id for c in connections], "profile": profile.id, "batch": batch.id, "contents": contents}

    etsy = FakeEtsy()
    monkeypatch.setattr(publish_worker, "httpx", _Httpx(etsy))
    monkeypatch.setattr(publish_worker, "get_cipher", lambda: cipher)
    monkeypatch.setattr(etsy_api, "transient_wait", lambda attempt: 0.0)
    monkeypatch.setattr(publisher, "ADOPT_WAIT_SECONDS", 0.0)
    redis = FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=100000, pause_percent=90)
    queue = Queue()

    async def _session():
        async with sm() as s:
            yield s

    from app.core.sessions import SessionStore

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_enqueuer] = lambda: queue
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_quota] = lambda: quota
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=BROWSER_HEADERS)
    authenticate(client, await open_session(redis, ids["tenant"]))
    enqueued: list[tuple] = []

    async def enqueue(function: str, *args: Any, **kwargs: Any) -> None:
        enqueued.append((function, *args))

    ctx = {"sessionmaker": sm, "bucket": TokenBucket(redis, rate=10_000.0), "quota": quota, "redis": redis, "enqueue": enqueue}
    return {**ids, "sm": sm, "etsy": etsy, "client": client, "queue": queue, "ctx": ctx, "enqueued": enqueued, "engine": engine}


async def _run_drafts(w: dict, job_ids: list[str]) -> None:
    for job_id in job_ids:
        assert await publish_worker.run_publish_job(w["ctx"], job_id) == "succeeded", job_id


async def _send_drafts(w: dict) -> list[str]:
    res = await w["client"].post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"})
    assert res.status_code == 200, res.text
    return [j["job_id"] for j in res.json()["jobs"]]


async def test_one_shop_seller_end_to_end(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=3)
    a = w["client"]
    state = (await a.get(f"/api/batches/{w['batch']}/send")).json()
    assert len(state["shops"]) == 1 and state["groups"] == [] and state["offer_all_shops"] is False
    preview = (await a.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert not preview["left_out"], preview["left_out"]
    assert preview["sentence"] == "3 listings → 1 shop: 3 drafts." and preview["button"] == "Create 3 drafts"
    await _run_drafts(w, await _send_drafts(w))
    assert len(w["etsy"].listings) == 3 and {d["state"] for d in w["etsy"].listings.values()} == {"draft"}
    await _creativity_checked(w)
    # Sent again: nothing new.
    again = (await a.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert again["total"] == 0 and again["disabled"].startswith("Nothing new to send")

    # Spread the drafts' go-lives out, then the Scheduled page.
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(), "per_shop_per_day": 2,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    spread = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert spread.status_code == 200 and spread.json()["scheduled"] == 3
    data = (await a.get("/api/scheduled")).json()
    assert len(data["items"]) == 3 and max(Counter(i["local_day"] for i in data["items"]).values()) <= 2
    keys = [i["key"] for i in data["items"]]
    moved = (await a.post("/api/scheduled/apply", json={"op": "shift", "keys": keys[:1], "params": {"minutes": 15}})).json()
    assert moved["changed"] == 1
    await a.post("/api/scheduled/restore", json={"undo": moved["undo"]})
    # Publish one now: its draft is there already, so its go-live is set, and the go-live job takes it live.
    now = (await a.post("/api/scheduled/apply", json={"op": "publish_now", "keys": keys[:1]})).json()
    assert now["changed"] == 1
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication).where(ListingPublication.scheduled_for.is_not(None)))).scalars().first()
        assert publication is not None
        publication.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)  # its time has come
        await s.commit()
    await release_scheduled_publishes(w["ctx"])
    live_jobs = [args[1] for args in w["enqueued"] if args[0] == "run_publish_live_job"]
    assert live_jobs
    assert await publish_worker.run_publish_live_job(w["ctx"], live_jobs[0]) == "succeeded"
    assert sorted(d["state"] for d in w["etsy"].listings.values()) == ["active", "draft", "draft"]

    # Personalization for a selection; the Shops table for the one shop.
    res = await a.post(f"/api/batches/{w['batch']}/personalization",
                       json={"personalization": {"enabled": True, "question_text": "Name", "required": False}, "content_ids": [str(w["contents"][1])]})
    assert sum(1 for c in res.json() if c["personalization_source"] == "listing") == 1
    table = (await a.get(f"/api/profiles/{w['profile']}/shops")).json()
    assert [r["main"] for r in table["rows"]] == [True]
    await w["engine"].dispose()


async def test_fifteen_shops_six_groups_end_to_end(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=15, listings=4)
    a, shops = w["client"], [str(s) for s in w["shops"]]
    layout = [shops[0:4], shops[3:7], shops[7:10], shops[10:13], shops[12:15], shops[0:15:3]]  # overlapping groups
    groups = []
    for n, members in enumerate(layout):
        res = await a.post("/api/shop-groups", json={"name": f"GROUP {n + 1}", "connection_ids": members})
        assert res.status_code == 201, res.text
        groups.append(next(g["id"] for g in res.json()["groups"] if g["name"] == f"GROUP {n + 1}"))
    assert len((await a.get(f"/api/batches/{w['batch']}/send")).json()["groups"]) == 6
    # Two groups sharing Shop 04: every listing to every shop of both, each shop once.
    await a.put(f"/api/batches/{w['batch']}/send-to", json={"send_to": {"group_ids": groups[:2]}})
    union = set(layout[0]) | set(layout[1])
    preview = (await a.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert (preview["listings"], preview["shops"], preview["total"]) == (4, len(union), 4 * len(union))
    await _run_drafts(w, await _send_drafts(w))
    drafts = w["etsy"].listings.values()
    assert len(drafts) == 4 * len(union)
    assert Counter((d["shop"], d["title"]) for d in drafts).most_common(1)[0][1] == 1  # never twice in one shop
    async with w["sm"]() as s:
        assert len(list((await s.execute(select(ListingPublication))).scalars())) == 4 * len(union)
        failed = [j.last_error for j in (await s.execute(select(Job))).scalars() if j.status is not JobStatus.succeeded]
    assert not failed, failed
    await _creativity_checked(w)  # every draft in all 7 shops

    # One listing also to GROUP 6 alone; the rest spread out over the remaining shops of GROUP 3.
    await a.put(f"/api/batches/{w['batch']}/send-to", json={"send_to": {"group_ids": [groups[2]]}})
    async with w["sm"]() as s:
        for shop_id in layout[2][:1]:
            (await s.get(EtsyConnection, uuid.UUID(shop_id))).daily_limit = 1
        await s.commit()
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(), "per_shop_per_day": 3,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 20, "stagger_minutes": 3}
    spread = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert spread.status_code == 200 and spread.json()["scheduled"] == 4 * len(layout[2])
    data = (await a.get("/api/scheduled")).json()
    items = data["items"]
    by_design: dict[str, set[str]] = {}
    for i in items:
        minute = i["at"][:16]
        assert minute not in by_design.setdefault(i["content_id"], set()), "a design live in two shops at the same minute"
        by_design[i["content_id"]].add(minute)
    limited = [i for i in items if i["shop_id"] == layout[2][0]]
    assert max(Counter(i["local_day"] for i in limited).values()) == 1  # that shop's daily limit
    # Pausing GROUP 3 holds its planned drafts; resuming lets them go.
    await a.put(f"/api/scheduled/groups/{groups[2]}", json={"paused": True})
    later = datetime.now(timezone.utc) + timedelta(days=30)
    async with w["sm"]() as s:
        assert await plans.release_due(s, w["ctx"]["enqueue"], now=later) == 0
    await a.put(f"/api/scheduled/groups/{groups[2]}", json={"paused": False})
    async with w["sm"]() as s:
        assert await plans.release_due(s, w["ctx"]["enqueue"], now=later) == 4 * len(layout[2])
        assert {x.state for x in (await s.execute(select(PlannedSlot))).scalars()} == {"drafting"}
    # The profile's Shops table: every shop it is used in.
    table = (await a.get(f"/api/profiles/{w['profile']}/shops")).json()
    assert len(table["rows"]) == 15 and table["rows"][0]["main"] is True
    await w["engine"].dispose()
