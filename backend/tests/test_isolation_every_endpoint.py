"""Account isolation, every endpoint (quality pass 2026-10, docs/qa-2026-10.md).

Two accounts, A and B, each own one of everything: a shop, a shop group, a profile, an upload
batch with a photo and a written listing, a draft on Etsy, a group schedule with a slot, a job, an
unfinished draft, a statement import and the whole mockup chain (template, subgroup, photo,
preset, design, design file, run, output, ZIP). Signed in as B, the test then walks **every**
route the app serves (read from the app itself, so a new endpoint is covered the day it is added):

1. With A's ids in the path, every request is refused: 403, 404 or 422 (never 2xx, 409 or 500).
   The body is generated from the endpoint's own request schema, so the handler is reached rather
   than stopped by validation.
2. With B's own ids in the path and A's ids in every id field of the body, nothing of A's is read,
   changed or referenced.
3. List endpoints, given A's ids as query parameters, never return them.

After all of that: none of A's rows changed, none of B's rows holds one of A's ids, no job was
queued with one of A's ids, and every admin route answers B (not an admin) with 404.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest_asyncio
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.crypto import TokenCipher
from app.core.ratelimit import rate_limit
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.base import Base
from app.db import models as M
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, make_tenant, open_session
from tests.support import VALID_TITLE

NOW = datetime.now(timezone.utc)
#: Refused: not found, forbidden, or the body/params were not acceptable.
REFUSED = {403, 404, 422}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args, kwargs))


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 50), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


async def _seed(sm, storage: LocalStorage, email: str, n: int) -> dict[str, Any]:
    """One of everything for one account; returns path-parameter name -> id."""
    tid = await make_tenant(sm, email)
    async with sm() as s:
        shop = M.EtsyConnection(tenant_id=tid, status=M.ConnectionStatus.active, etsy_user_id=n, shop_id=n,
                                shop_name=f"Shop{n}")
        s.add(shop)
        await s.flush()
        group = M.ShopGroup(tenant_id=tid, name=f"Group{n}")
        s.add(group)
        await s.flush()
        s.add(M.ShopGroupMember(group_id=group.id, connection_id=shop.id))
        profile = M.ListingProfile(tenant_id=tid, connection_id=shop.id, name=f"Profile{n}", reference_listing_id=n,
                                   content_template="apparel", confirmed=True)
        batch = M.UploadBatch(tenant_id=tid, status=M.UploadBatchStatus.ready, file_count=1, name=f"Batch{n}")
        s.add_all([profile, batch])
        await s.flush()
        key = f"{tid}/{batch.id}/processed/design.jpg"
        storage.put(key, _png(), "image/jpeg")
        asset = M.Asset(tenant_id=tid, batch_id=batch.id, original_filename=f"SKU{n}_front.png", parsed_sku=f"SKU{n}",
                        group_key=f"SKU{n}", rank=1, status=M.AssetStatus.processed, storage_key=key, processed_key=key,
                        mime_type="image/jpeg", width=40, height=50)
        s.add(asset)
        await s.flush()
        s.add(M.ListingGroupSetting(tenant_id=tid, batch_id=batch.id, group_key=f"SKU{n}", profile_id=profile.id))
        content = M.GeneratedContent(tenant_id=tid, batch_id=batch.id, asset_id=asset.id, listing_profile_id=profile.id,
                                     title=VALID_TITLE, tags=[f"tag{i}" for i in range(13)], description="A description.",
                                     approved=True)
        s.add(content)
        await s.flush()
        publication = M.ListingPublication(tenant_id=tid, content_id=content.id, connection_id=shop.id,
                                           profile_id=profile.id, etsy_listing_id=n * 10, state="draft")
        job = M.Job(tenant_id=tid, connection_id=shop.id, type=M.JobType.create_draft, status=M.JobStatus.queued,
                    payload={"content_id": str(content.id)}, batch_id=batch.id)
        plan = M.GroupPlan(tenant_id=tid)
        s.add_all([publication, job, plan])
        await s.flush()
        s.add(M.PlannedSlot(tenant_id=tid, plan_id=plan.id, connection_id=shop.id, content_id=content.id,
                            draft_at=NOW + timedelta(days=2), publish_at=NOW + timedelta(days=2, hours=2)))
        attempt = M.DraftAttempt(tenant_id=tid, content_id=content.id, connection_id=shop.id, etsy_listing_id=n * 10 + 1)
        s.add(attempt)
        s.add(M.ShopListingCache(tenant_id=tid, connection_id=shop.id, listing_id=n * 10 + 2,
                                 payload={"listing_id": n * 10 + 2, "state": "active", "title": f"Listing{n}"}))
        s.add(M.StatementImport(tenant_id=tid, connection_id=shop.id, month=date(2026, 9, 1), rows=1,
                                first_day=date(2026, 9, 1), last_day=date(2026, 9, 30), net_minor=100)) \
            if hasattr(M.StatementImport, "month") else None
        template = M.MockupTemplate(tenant_id=tid, name=f"Template{n}")
        preset = M.MockupSubgroupPreset(tenant_id=tid, name=f"Preset{n}")
        s.add_all([template, preset])
        await s.flush()
        subgroup = M.MockupSubgroup(tenant_id=tid, template_id=template.id, name=f"Sub{n}")
        s.add(subgroup)
        await s.flush()
        photo_key = f"{tid}/mockups/photo.jpg"
        storage.put(photo_key, _png(), "image/jpeg")
        photo = M.MockupPhoto(tenant_id=tid, template_id=template.id, subgroup_id=subgroup.id, name=f"Photo{n}",
                              rights_confirmed_at=NOW, storage_key=photo_key) if hasattr(M.MockupPhoto, "storage_key") else \
            M.MockupPhoto(tenant_id=tid, template_id=template.id, subgroup_id=subgroup.id, name=f"Photo{n}", rights_confirmed_at=NOW)
        design = M.MockupDesign(tenant_id=tid, batch_id=batch.id, group_key=f"SKU{n}", original_filename=f"D{n}.png",
                                width=40, height=50)
        s.add_all([photo, design])
        await s.flush()
        version = M.MockupDesignVersion(tenant_id=tid, design_id=design.id, original_filename=f"D{n}-dark.png",
                                        width=40, height=50)
        run = M.MockupRun(tenant_id=tid, batch_id=batch.id, template_ids=[str(template.id)], photo_ids=[str(photo.id)])
        s.add_all([version, run])
        await s.flush()
        output = M.MockupOutput(tenant_id=tid, run_id=run.id, batch_id=batch.id, design_id=design.id,
                                template_name=f"Template{n}", position=1, filename=f"o{n}.jpg", width=40, height=50,
                                byte_size=10)
        zipped = M.MockupZip(tenant_id=tid, run_id=run.id)
        s.add_all([output, zipped])
        await s.commit()
        return {
            "tenant": tid, "connection_id": shop.id, "shop_id": shop.id, "group_id": group.id, "profile_id": profile.id,
            "batch_id": batch.id, "asset_id": asset.id, "content_id": content.id, "job_id": job.id, "plan_id": plan.id,
            "record_id": str(attempt.id), "listing_id": n * 10 + 2, "month": "2026-09", "template_id": template.id,
            "subgroup_id": subgroup.id, "photo_id": photo.id, "preset_id": preset.id, "design_id": design.id,
            "version_id": version.id, "run_id": run.id, "output_id": output.id, "zip_id": zipped.id,
            "key": "creativity_production", "publication_id": publication.id,
        }


@pytest_asyncio.fixture()
async def world(tmp_path) -> AsyncIterator[dict]:
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path)
    redis = FakeAsyncRedis()
    recorder = Recorder()

    async def _session():
        async with sm() as s:
            yield s

    async def _no_limit() -> None:
        return None

    app = create_app()
    o = app.dependency_overrides
    o[deps.get_session] = _session
    o[deps.get_storage] = lambda: storage
    o[deps.get_redis] = lambda: redis
    o[deps.get_session_store] = lambda: SessionStore(redis)
    o[deps.get_enqueuer] = lambda: recorder
    o[deps.get_quota] = lambda: DailyQuota(redis, global_daily_limit=100_000)
    o[deps.get_connection_service] = lambda: ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k",
                                                               token_url="https://t")
    o[deps.get_ingestor] = lambda: BatchIngestor(sessionmaker=sm, storage=storage,
                                                 processor=ImageProcessor(backend=PillowBackend()), sku_parser=SkuParser())
    o[rate_limit] = _no_limit  # hundreds of requests from one client in a few seconds

    a = await _seed(sm, storage, "a@example.com", 1001)
    b = await _seed(sm, storage, "b@example.com", 2002)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as client:
        client.cookies.set(SESSION_COOKIE, await open_session(redis, b["tenant"]))
        yield {"app": app, "client": client, "a": a, "b": b, "sm": sm, "recorder": recorder, "redis": redis}
    await engine.dispose()


# --- request generation -------------------------------------------------------------------------


def _routes(app) -> list[tuple[str, str, dict]]:
    spec = app.openapi()
    out = []
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, op in ops.items():
            if method in ("get", "post", "put", "patch", "delete"):
                out.append((method, path, op))
    return sorted(out, key=lambda r: (r[1], r[0]))


def _resolve(spec: dict, schema: Any) -> Any:
    while isinstance(schema, dict) and "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


#: Body field name -> the kind of id it holds (A's id goes in for the second sweep).
ID_FIELDS = {
    "profile_id": "profile_id", "profile_ids": "profile_id", "connection_id": "connection_id",
    "connection_ids": "connection_id", "shop_id": "connection_id", "shop_ids": "connection_id",
    "content_id": "content_id", "content_ids": "content_id", "group_id": "group_id", "group_ids": "group_id",
    "batch_id": "batch_id", "asset_id": "asset_id", "asset_ids": "asset_id", "photo_id": "photo_id",
    "photo_ids": "photo_id", "template_id": "template_id", "template_ids": "template_id",
    "design_id": "design_id", "design_ids": "design_id", "subgroup_id": "subgroup_id", "preset_id": "preset_id",
    "run_id": "run_id", "record_ids": "record_id", "attempt_ids": "record_id", "keys": "publication_id",
    "covers": "photo_id", "source_photo_id": "photo_id", "target_photo_ids": "photo_id", "plan_id": "plan_id",
}


def _sample(spec: dict, schema: Any, name: str, ids: dict | None, depth: int = 0) -> Any:
    schema = _resolve(spec, schema)
    if depth > 6 or not isinstance(schema, dict):
        return None
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(spec, o).get("type") != "null"]
            return _sample(spec, options[0], name, ids, depth + 1) if options else None
    if "allOf" in schema:
        return _sample(spec, schema["allOf"][0], name, ids, depth + 1)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if ids is not None and name in ID_FIELDS and ID_FIELDS[name] in ids:
        value = ids[ID_FIELDS[name]]
        value = value if isinstance(value, int) else str(value)
        return [value] if kind == "array" else value
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        wanted = set(schema.get("required", [])) | ({k for k in props if k in ID_FIELDS} if ids is not None else set())
        return {k: _sample(spec, props[k], k, ids, depth + 1) for k in props if k in wanted}
    if kind == "array":
        item = _sample(spec, schema.get("items", {}), name, ids, depth + 1)
        return [item] * max(1, schema.get("minItems", 1)) if item is not None else []
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return max(1.0, float(schema.get("minimum", 1)))
    if kind == "boolean":
        return True
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "uuid":
            return str(uuid.uuid4())
        if fmt == "date":
            return "2026-11-01"
        if fmt == "date-time":
            return (NOW + timedelta(days=3)).isoformat()
        if fmt == "binary":
            return None
        return "x" * max(1, schema.get("minLength", 1))
    return None


def _request(spec: dict, method: str, path: str, op: dict, path_ids: dict, body_ids: dict | None) -> dict:
    url = re.sub(r"\{([^}]+)\}", lambda m: str(path_ids.get(m.group(1), uuid.uuid4())), path)
    kw: dict[str, Any] = {}
    params = {}
    for p in op.get("parameters", []):
        if p.get("in") == "query" and p.get("required"):
            params[p["name"]] = _sample(spec, p.get("schema", {}), p["name"], body_ids) or "x"
    if params:
        kw["params"] = params
    content = (op.get("requestBody") or {}).get("content", {})
    if "application/json" in content:
        kw["json"] = _sample(spec, content["application/json"]["schema"], "", body_ids) or {}
    elif "multipart/form-data" in content:
        schema = _resolve(spec, content["multipart/form-data"]["schema"])
        files, data = {}, {}
        for k, v in (schema.get("properties") or {}).items():
            v = _resolve(spec, v)
            binary = v.get("format") == "binary" or _resolve(spec, v.get("items", {})).get("format") == "binary"
            if binary:
                files[k] = ("x.png", _png(), "image/png")
            elif k in schema.get("required", []):
                data[k] = str(_sample(spec, v, k, body_ids))
        kw["files"] = files or None
        kw["data"] = data or None
    return {"method": method.upper(), "url": url, **kw}


# --- what A owns, before and after --------------------------------------------------------------


def _id_strings(ids: dict) -> set[str]:
    return {str(v) for k, v in ids.items() if isinstance(v, uuid.UUID)}


def _leaks(res, a_ids: set[str], sent: dict) -> list[str]:
    """What of A's a response shows: A's names, or A's ids the request did not itself send
    (a refusal that echoes the id it was given shows nothing)."""
    if not res.headers.get("content-type", "").startswith("application/json"):
        return []
    echoed = json.dumps({k: sent.get(k) for k in ("url", "json", "params", "data")}, default=str)
    found = [i for i in a_ids if i in res.text and i not in echoed]
    found += [n for n in A_NAMES if n in res.text]
    return found


#: A's names in the seed: none may ever reach B.
A_NAMES = ("Shop1001", "Group1001", "Profile1001", "Batch1001", "SKU1001", "Listing1001", "Template1001",
           "Preset1001", "Sub1001", "Photo1001", "D1001.png", "a@example.com")


async def _snapshot(sm, tenant_id: uuid.UUID) -> dict[str, list]:
    """Every row of every table that has a tenant_id column, for one account."""
    out: dict[str, list] = {}
    async with sm() as s:
        for table in Base.metadata.sorted_tables:
            if "tenant_id" not in table.c:
                continue
            rows = (await s.execute(select(table).where(table.c.tenant_id == tenant_id))).mappings().all()
            out[table.name] = sorted((json.dumps({k: v for k, v in r.items()}, default=str, sort_keys=True) for r in rows))
    return out


#: Routes that end B's own session or account: not swept with B's ids (A's are still tried).
OWN_ACCOUNT = {"/api/account/logout", "/api/account/delete", "/api/account/password", "/api/account/login",
               "/api/account/register"}


def _last(route: tuple[str, str, dict]) -> tuple:
    """Deletes and disconnects last, so B's rows exist while the other routes are swept."""
    method, path, _ = route
    return (method == "delete" or path.endswith(("/disconnect", "/delete")), path, method)


def _admin(path: str) -> bool:
    return path.startswith("/api/admin/") or path.startswith("/api/account/admin/")


# --- the test -----------------------------------------------------------------------------------


async def test_account_b_cannot_reach_anything_of_account_a(world) -> None:
    app, client, a, b, sm = world["app"], world["client"], world["a"], world["b"], world["sm"]
    spec = app.openapi()
    a_ids = _id_strings(a)
    before = await _snapshot(sm, a["tenant"])
    leaks: list[str] = []

    redis = world["redis"]
    for method, path, op in sorted(_routes(app), key=_last):
        if _admin(path):
            continue
        # A fresh session for every request: one route may end the last one (logout, password).
        client.cookies.set(SESSION_COOKIE, await open_session(redis, b["tenant"]))
        has_ids = "{" in path
        # 1. A's ids in the path.
        # "month" is a calendar month, not something A owns: those routes answer for B's own month.
        if has_ids and not {"month"} & set(re.findall(r"\{([^}]+)\}", path)):
            req = _request(spec, method, path, op, a, None)
            res = await client.request(**req)
            if res.status_code not in REFUSED:
                leaks.append(f"{method.upper()} {path} with A's ids -> {res.status_code} {res.text[:160]}")
            elif _leaks(res, a_ids, req):
                leaks.append(f"{method.upper()} {path} with A's ids -> {res.status_code} shows {_leaks(res, a_ids, req)}")
        # 2. B's own path, A's ids in the body and query.
        if path in OWN_ACCOUNT:
            continue
        req = _request(spec, method, path, op, b, a)
        res = await client.request(**req)
        if res.status_code >= 500:
            leaks.append(f"{method.upper()} {path} with A's ids in the body -> {res.status_code}")
        if _leaks(res, a_ids, req):
            leaks.append(f"{method.upper()} {path} with A's ids in the body -> {res.status_code} shows {_leaks(res, a_ids, req)}")
        # 3. A list asked about A's things.
        if method == "get" and not has_ids:
            params = {"shop": str(a["connection_id"]), "batch_id": str(a["batch_id"]),
                      "profile_id": str(a["profile_id"]), "group_id": str(a["group_id"])}
            res = await client.get(path, params=params)
            if res.status_code < 300 and _leaks(res, a_ids, {"params": params}):
                leaks.append(f"GET {path}?shop=<A> -> {res.status_code} shows {_leaks(res, a_ids, {'params': params})}")

    assert not leaks, "\n".join(leaks)

    # Nothing of A's changed.
    after = await _snapshot(sm, a["tenant"])
    changed = [t for t in before if before[t] != after.get(t)]
    assert not changed, f"A's rows changed in: {changed}"

    # None of B's rows points at one of A's things.
    b_rows = await _snapshot(sm, b["tenant"])
    pointing = [t for t, rows in b_rows.items() for r in rows if any(i in r for i in a_ids)]
    assert not pointing, f"B's rows that hold one of A's ids: {sorted(set(pointing))}"

    # No job was queued about A's things.
    queued = [c for c in world["recorder"].calls if any(i in json.dumps(c, default=str) for i in a_ids)]
    assert not queued, f"jobs queued with A's ids: {queued}"


async def test_every_admin_route_is_404_for_a_seller(world) -> None:
    app, client, spec = world["app"], world["client"], world["app"].openapi()
    wrong = []
    for method, path, op in _routes(app):
        if not _admin(path):
            continue
        res = await client.request(**_request(spec, method, path, op, {"tenant_id": world["a"]["tenant"]}, None))
        if res.status_code != 404:
            wrong.append(f"{method.upper()} {path} -> {res.status_code}")
    assert not wrong, "\n".join(wrong)
    assert any(_admin(p) for _, p, _ in _routes(app))


async def test_the_sweep_reaches_every_route(world) -> None:
    """The route list comes from the app: a new endpoint joins the sweep without editing this file."""
    routes = _routes(world["app"])
    assert len(routes) >= 240
    params = {n for _, p, _ in routes for n in re.findall(r"\{([^}]+)\}", p) if not _admin(p)}
    unknown = params - set(world["a"])
    assert not unknown, f"path parameters the sweep has no id for: {unknown}"


async def test_snapshot_sees_every_tenant_table(world) -> None:
    """Sanity: the before/after comparison covers the tables the seed filled."""
    snap = await _snapshot(world["sm"], world["a"]["tenant"])
    for table in ("listing_profile", "upload_batch", "generated_content", "listing_publication", "mockup_template",
                  "mockup_photo", "mockup_run", "planned_slot", "shop_group"):
        assert snap.get(table), table
    async with world["sm"]() as s:
        assert (await s.execute(text("select count(*) from tenant"))).scalar_one() == 2
