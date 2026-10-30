"""The app, with Etsy and the AI model answered from memory: the load test's entry point.

    uvicorn scripts.load.stub:app --workers 2
    arq scripts.load.stub.WorkerSettings

ETSY_CALLS_BLOCKED stays on. Importing this module, in the load stack only:

* gives every ``httpx.AsyncClient`` in the process the in-memory transport below, so no request
  of any kind leaves (the model's SDK uses httpx too);
* lets through the guard only the API client's own calls ("GET /path"), which that transport
  answers; a token refresh, a token exchange and an image fetch stay blocked, as locally.

It refuses to load unless the switch is on and the database is the load stack's own.

Etsy here answers in LOAD_ETSY_MS (default 120-350 ms), with 0.2% 429 and 0.5% 5xx, and counts what
it is sent. The model answers in LOAD_MODEL_MS (default 6-16 s: an assumption, not a measurement).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from typing import Any
from urllib.parse import parse_qs

import httpx
import httpx2

from app.core.config import get_settings

_settings = get_settings()
if not _settings.etsy_calls_blocked or not _settings.database_url.endswith("@load-postgres:5432/listyro_load"):
    raise SystemExit("load stub: only in the load stack, with ETSY_CALLS_BLOCKED on: refusing to start")

from app.etsy import guard  # noqa: E402

_real_check = guard.check


def _check(what: str = "") -> None:
    if what.split(" ", 1)[0] in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return  # the API client's own request: it goes to the transport below
    _real_check(what)


guard.check = _check


def _range(name: str, default: str) -> tuple[float, float]:
    low, high = os.environ.get(name, default).split(",")
    return float(low) / 1000, float(high) / 1000


ETSY_S = _range("LOAD_ETSY_MS", "120,350")
MODEL_S = _range("LOAD_MODEL_MS", "6000,16000")
rnd = random.Random()

counts: Counter[str] = Counter()
per_second: Counter[int] = Counter()
per_account: Counter[str] = Counter()
listings: dict[int, dict[str, Any]] = {}
_ids = iter(range(5_000_000_000 + os.getpid() * 100_000, 10**12))

_counting: list[asyncio.Task] = []
_profiling: list[asyncio.Task] = []
#: Every shop's own settings: the seed's main-shop ids and other-shop ids (scripts/load/seed.py).
_SETTINGS = {
    "shipping-profiles": [{"shipping_profile_id": i, "title": "Standard", "origin_country_iso": "US"} for i in (55, 155)],
    "policies/return": [{"return_policy_id": i, "accepts_returns": True, "accepts_exchanges": True, "return_deadline": 30} for i in (88, 188)],
    "readiness-state-definitions": [{"readiness_state_id": i, "readiness_state": "made_to_order", "min_processing_time": 1,
                                     "max_processing_time": 3, "processing_time_unit": "business_days"} for i in (42, 142)],
}
TITLE = ("Retro Sunset Graphic Shirt, Vintage Mountain Tee for Hikers, Nature Lover Camping "
         "Top, Outdoor Adventure Gift Idea Clothing")
TAGS = ["retro sunset shirt", "vintage mountain", "hiking tee", "nature lover top", "camping shirt", "outdoor adventure",
        "mountain graphic", "hiker gift idea", "sunset graphic tee", "adventure clothing", "trail lover", "forest camping",
        "wilderness apparel"]
VISION = {"themes": ["mountains", "sunset"], "theme": "mountains", "embedded_text": "", "style": "retro", "colors": ["orange"],
          "target_audience": "hikers", "product_type_hints": ["t-shirt"], "occasion": "", "meaning": "a love of hiking",
          "recipient": "hiker", "humor_type": "", "characters": [], "profession": "", "season": "", "neckline": "",
          "sleeve_length": "", "clothing_style": ""}


async def _model(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    await asyncio.sleep(rnd.uniform(*MODEL_S))
    is_vision = any(b.get("type") == "image" for b in body["messages"][0]["content"])
    counts["model_vision" if is_vision else "model_content"] += 1
    answer = VISION if is_vision else {"title": TITLE, "tags": TAGS, "description": "A soft tee with a retro mountain print."}
    return httpx2.Response(200, json={
        "id": "msg_stub", "type": "message", "role": "assistant", "model": body.get("model", "stub"), "stop_reason": "end_turn",
        "stop_sequence": None, "content": [{"type": "text", "text": json.dumps(answer)}],
        "usage": {"input_tokens": 1500, "output_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


def _listing(lid: int) -> dict[str, Any]:
    """A listing the stub made, or (for the seed's) a draft, except a few the seller changed."""
    row = listings.get(lid)
    if row is not None:
        return row
    state = "active" if lid % 37 == 0 else "draft"  # published in Shop Manager (etsy/reconcile.py)
    row = {"listing_id": lid, "title": "Retro Graphic Tee", "state": state, "state_timestamp": int(time.time()) - 600,
           "images": [], "inventory": None}
    listings[lid] = row
    return row


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("images", "inventory", "personalization")}


#: The server's uplink: 14 Mbps. Images sent to Etsy take turns on it, as on the real line.
UPLINK_BYTES_PER_SECOND = 14e6 / 8
_uplink_free_at = 0.0


async def _through_the_uplink(size: int) -> None:
    global _uplink_free_at
    now = time.monotonic()
    _uplink_free_at = max(_uplink_free_at, now) + size / UPLINK_BYTES_PER_SECOND
    counts["uplink_wait_ms"] += int((_uplink_free_at - now) * 1000)
    await asyncio.sleep(_uplink_free_at - now)


async def _etsy(request: httpx.Request) -> httpx.Response:
    now = int(time.time())
    counts["etsy"] += 1
    per_second[now] += 1
    account = re.match(r"Bearer (\d+)\.", request.headers.get("authorization", ""))
    if account:
        per_account[str(int(account.group(1)) // 10)] += 1
    await asyncio.sleep(rnd.uniform(*ETSY_S))
    roll = rnd.random()
    if roll < 0.002:
        counts["etsy_429"] += 1
        return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "Exceeded per second rate limit"})
    if roll < 0.007:
        counts["etsy_5xx"] += 1
        return httpx.Response(503, json={"error": "unavailable"})
    method, path = request.method, request.url.path.split("/v3/application", 1)[1]
    form = {k: v[0] for k, v in parse_qs(request.content.decode("latin-1")).items()} \
        if request.headers.get("content-type", "").startswith("application/x-www-form") else {}
    if method == "POST" and (m := re.fullmatch(r"/shops/(\d+)/listings", path)):
        lid = next(_ids)
        listings[lid] = {
            "listing_id": lid, "shop_id": int(m.group(1)), "title": form.get("title", ""), "state": "draft",
            "state_timestamp": now, "original_creation_timestamp": now, "taxonomy_id": int(form.get("taxonomy_id") or 0),
            "who_made": form.get("who_made"), "when_made": form.get("when_made"), "tags": (form.get("tags") or "").split(","),
            "production_partners": [{"production_partner_id": int(p)} for p in (form.get("production_partner_ids") or "").split(",") if p],
            "materials": (form.get("materials") or "").split(",") if form.get("materials") else [],
            "shop_section_id": int(form["shop_section_id"]) if form.get("shop_section_id") else None,
            "images": [], "inventory": None,
        }
        counts["etsy_drafts"] += 1
        return httpx.Response(201, json={"listing_id": lid})
    if method == "POST" and (m := re.fullmatch(r"/shops/\d+/listings/(\d+)/images", path)):
        row = _listing(int(m.group(1)))
        counts["etsy_image_bytes"] += len(request.content)
        counts["etsy_images"] += 1
        await _through_the_uplink(len(request.content))
        image = {"listing_image_id": next(_ids), "url_fullxfull": "http://stub.invalid/i.jpg", "rank": len(row["images"]) + 1}
        row["images"].append(image)
        return httpx.Response(201, json=image)
    if method == "DELETE":
        return httpx.Response(204)
    if method == "GET" and path == "/listings/batch":
        ids = [int(i) for i in (request.url.params.get("listing_ids") or "").split(",") if i]
        return httpx.Response(200, json={"count": len(ids), "results": [_public(_listing(i)) for i in ids]})
    if m := re.fullmatch(r"/listings/(\d+)/images", path):
        images = _listing(int(m.group(1)))["images"]
        return httpx.Response(200, json={"count": len(images), "results": images})
    if m := re.fullmatch(r"/listings/(\d+)/inventory", path):
        row = _listing(int(m.group(1)))
        if method == "GET":
            return httpx.Response(200, json=row["inventory"] or {"products": []})
        row["inventory"] = json.loads(request.content)
        return httpx.Response(200, json=row["inventory"])
    if m := re.fullmatch(r"/listings/(\d+)/properties", path):
        return httpx.Response(200, json={"count": 0, "results": []})
    if m := re.fullmatch(r"/shops/\d+/listings/(\d+)/properties/\d+", path):
        return httpx.Response(200, json={})
    if m := re.fullmatch(r"/listings/(\d+)/personalization", path):
        row = _listing(int(m.group(1)))
        if method != "GET":
            row["personalization"] = json.loads(request.content)
        return httpx.Response(200, json=row.get("personalization") or {"personalization_questions": []})
    if m := re.fullmatch(r"/shops/\d+/listings/(\d+)", path):  # updateListing
        row = _listing(int(m.group(1)))
        for key, value in form.items():
            row[key] = value
        if form.get("state"):
            row["state"], row["state_timestamp"] = form["state"], now
            counts["etsy_published"] += form["state"] == "active"
        return httpx.Response(200, json=_public(row))
    if m := re.fullmatch(r"/listings/(\d+)", path):
        return httpx.Response(200, json=_public(_listing(int(m.group(1)))))
    if m := re.fullmatch(r"/shops/\d+/(shipping-profiles|policies/return|readiness-state-definitions)", path):
        return httpx.Response(200, json={"count": 2, "results": _SETTINGS[m.group(1)]})
    if re.fullmatch(r"/shops/\d+/(sections|production-partners)", path) or path.startswith("/seller-taxonomy/"):
        return httpx.Response(200, json={"count": 0, "results": []})
    if re.fullmatch(r"/shops/\d+/listings(/active)?", path):
        return httpx.Response(200, json={"count": 0, "results": []})
    if re.fullmatch(r"/shops/\d+", path):
        return httpx.Response(200, json={"shop_id": 1, "shop_name": "LoadShop", "currency_code": "USD"})
    counts[f"etsy_no_route {method} {re.sub(r'[0-9]+', '#', path)}"] += 1
    return httpx.Response(404, json={"error": f"no route for {method} {path}"})


async def _handle(request: httpx.Request) -> httpx.Response:
    if "/v3/application" in request.url.path:
        return await _etsy(request)
    counts["refused_other"] += 1
    return httpx.Response(599, json={"error": "the load stub answers only Etsy's API and the model"})


_transport = httpx.MockTransport(_handle)
_real_init = httpx.AsyncClient.__init__


def _init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
    kwargs["transport"] = _transport
    kwargs.pop("mounts", None)
    _real_init(self, *args, **kwargs)


httpx.AsyncClient.__init__ = _init  # type: ignore[method-assign]

# The model's SDK uses httpx2: its own transport, answering only the model's endpoint.
_real_init2 = httpx2.AsyncClient.__init__
_transport2 = httpx2.MockTransport(_model)


def _init2(self: httpx2.AsyncClient, *args: Any, **kwargs: Any) -> None:
    kwargs["transport"] = _transport2
    kwargs.pop("mounts", None)
    _real_init2(self, *args, **kwargs)


httpx2.AsyncClient.__init__ = _init2  # type: ignore[method-assign]


#: Connection-seconds per (minute, endpoint): who holds the pool.
held: Counter[tuple[int, str]] = Counter()
_route: contextvars.ContextVar[str] = contextvars.ContextVar("load_route", default="(worker or background)")


def _shape(path: str) -> str:
    return re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "{id}", path.split("?")[0]).removeprefix("/api/")


def _watch_pool(pool: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(pool, "checkout")
    def _out(dbapi_connection: Any, record: Any, proxy: Any) -> None:
        record.info["load_out"] = (time.monotonic(), _route.get())

    @event.listens_for(pool, "checkin")
    def _in(dbapi_connection: Any, record: Any) -> None:
        began = record.info.pop("load_out", None)
        if began is not None:
            held[(int(time.time()) // 60, began[1])] += time.monotonic() - began[0]


#: Where an API process's time goes in the first LOAD_PROFILE_SECONDS after its first request: CPU
#: per endpoint and per function, and where the requests in progress are suspended.
PROFILE_SECONDS = float(os.environ.get("LOAD_PROFILE_SECONDS", "0"))
cpu_route: Counter[str] = Counter()
cpu_function: Counter[str] = Counter()
waiting: Counter[str] = Counter()
_NO_ROUTE = "(worker or background)"


def _short(path: str) -> str:
    return path.split("/site-packages/")[-1].split("/app/app/")[-1].split("/usr/local/lib/python3.12/")[-1]


def _ours(path: str) -> bool:
    return "/site-packages/" not in path and "/lib/python3" not in path and "scripts/load" not in path


def _sample_cpu(loop: asyncio.AbstractEventLoop, main_thread: int, until: float) -> None:
    while time.monotonic() < until:
        time.sleep(0.02)
        frame = sys._current_frames().get(main_thread)
        # Waiting for something to do: in select, or with uvloop in C under the runner. (This thread
        # gets the GIL when the main one lets go, so calls that release it are over-counted.)
        if frame is None or frame.f_code.co_name in ("select", "run_until_complete") or (frame.f_code.co_name == "run" and "runners" in frame.f_code.co_filename):
            continue
        task = asyncio.current_task(loop)
        cpu_route[task.get_context().get(_route, _NO_ROUTE) if task is not None else "(the event loop itself)"] += 1
        seen: set[str] = set()
        for _ in range(100):
            if frame is None:
                break
            seen.add(f"{_short(frame.f_code.co_filename)}:{frame.f_code.co_name}")
            frame = frame.f_back
        cpu_function.update(seen)


def _suspended_at(task: asyncio.Task) -> str:
    """The last line of ours, and what it is waiting in."""
    ours = last = ""
    coro: Any = task.get_coro()
    for _ in range(100):
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None) or getattr(coro, "ag_frame", None)
        if frame is not None:
            path = frame.f_code.co_filename
            last = f"{_short(path)}:{frame.f_code.co_name}"
            if _ours(path):
                ours = f"{_short(path)}:{frame.f_lineno} {frame.f_code.co_name}"
        coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None) or getattr(coro, "ag_await", None)
        if coro is None:
            break
    return f"{ours or '-'} <- {last}"


async def _profile() -> None:
    loop = asyncio.get_running_loop()
    until = time.monotonic() + PROFILE_SECONDS
    threading.Thread(target=_sample_cpu, args=(loop, threading.get_ident(), until), daemon=True).start()
    while time.monotonic() < until:
        await asyncio.sleep(1)
        for task in asyncio.all_tasks(loop):
            route = task.get_context().get(_route, _NO_ROUTE)
            if route != _NO_ROUTE and task is not asyncio.current_task():
                waiting[f"{route} | {_suspended_at(task)}"] += 1


async def _publish_counts() -> None:
    """Every 5 s, what this process saw, for the driver (Redis hash ``load:stub``): the stub's counts,
    and per minute the event loop's worst lag (is the CPU the limit?) and the most database
    connections taken from the pool (is the pool the limit?)."""
    from redis.asyncio import Redis

    from app.db.session import get_engine

    redis = Redis.from_url(_settings.redis_url)
    me = str(os.getpid())
    role = "worker" if any("arq" in a for a in sys.argv) else "api"
    minutes: dict[int, dict[str, float]] = {}
    pool = get_engine().sync_engine.pool
    _watch_pool(pool)
    tick = 0
    while True:
        started = time.perf_counter()
        await asyncio.sleep(1)
        lag = time.perf_counter() - started - 1
        minute = minutes.setdefault(int(time.time()) // 60, {"lag": 0.0, "out": 0, "limit": pool.size() + pool._max_overflow})
        minute["lag"] = max(minute["lag"], lag)
        minute["out"] = max(minute["out"], pool.checkedout())
        tick += 1
        if tick % 5:
            continue
        try:
            await redis.hset("load:stub", me, json.dumps({
                "role": role, "counts": dict(counts), "per_account": dict(per_account),
                "seconds": {str(k): v for k, v in list(per_second.items())[-3600:]},
                "minutes": {str(k): v for k, v in list(minutes.items())[-120:]},
                "held": {f"{m} {k}": round(v, 2) for (m, k), v in list(held.items())[-4000:]},
                "cpu_route": dict(cpu_route), "cpu_function": dict(cpu_function.most_common(300)),
                "waiting": dict(waiting.most_common(300)),
            }))
        except Exception:  # noqa: BLE001 - a missed sample is only a missed sample
            pass


from app.main import app as _api  # noqa: E402
from app.workers.settings import WorkerSettings as _Worker  # noqa: E402


async def _count_from_the_start(scope: Any, receive: Any, send: Any) -> None:
    if not _counting:
        _counting.append(asyncio.get_running_loop().create_task(_publish_counts()))
    if scope.get("type") == "http":
        path = scope["path"]
        if PROFILE_SECONDS and not _profiling and path != "/health":
            # Outside the request's context, or every task it spawns would count as this request.
            _profiling.append(asyncio.get_running_loop().create_task(_profile(), context=contextvars.Context()))
        _route.set(f"{scope['method']} {_shape(path)}")
    await _api(scope, receive, send)


app = _count_from_the_start


async def _on_startup(ctx: dict[str, Any]) -> None:
    await _Worker.on_startup(ctx)
    _counting.append(asyncio.get_running_loop().create_task(_publish_counts()))


# arq reads a settings class's own attributes only, not inherited ones.
WorkerSettings = type("WorkerSettings", (), {
    **{k: v for k, v in vars(_Worker).items() if not k.startswith("__")}, "on_startup": _on_startup})
