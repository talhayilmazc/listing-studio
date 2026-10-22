"""Part B at volume (B11): 500 listings, 15 shops in 6 groups, and a 200-photo upload.

Times the screens and actions a large seller uses, in process against SQLite (the production
database is faster; these bounds are generous so a slow machine cannot fail them, and the
numbers are printed: ``-s`` shows them):

* the review page's listings and the batch list with their statuses;
* Where and when's preview for every listing to every shop (7,500 drafts) and spread out;
* sending spread out (7,500 planned go-lives), the Scheduled page reading them, and a bulk
  shift of 500 of them, previewed;
* 200 photos uploaded one request at a time, as the upload page sends them.
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.api import deps
from app.core.config import Settings
from app.pipeline.images import ImageProcessor
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.test_end_to_end import _world

LISTINGS, SHOPS, GROUPS = 500, 15, 6
TIMES: dict[str, float] = {}


async def _timed(name: str, call, bound: float):  # noqa: ANN001, ANN202
    # PROFILE=<part of a name>: where that one spends its time (cProfile, top 25 by own time).
    profile = cProfile.Profile() if os.environ.get("PROFILE") and os.environ["PROFILE"] in name else None
    start = time.perf_counter()
    if profile:
        profile.enable()
    result = await call
    if profile:
        profile.disable()
        pstats.Stats(profile).sort_stats("tottime").print_stats(25)
    TIMES[name] = took = time.perf_counter() - start
    print(f"{name}: {took:.2f} s")
    assert took < bound, f"{name} took {took:.1f} s (bound {bound} s)"
    return result


def _jpeg(n: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2400, 3000), (n % 255, 120, 200 - n % 100)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def test_five_hundred_listings_fifteen_shops_six_groups(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=SHOPS, listings=LISTINGS)
    a, shops, batch = w["client"], [str(s) for s in w["shops"]], w["batch"]
    groups = []
    for n in range(GROUPS):
        members = shops[n * 2: n * 2 + 5] or shops[-5:]
        res = await a.post("/api/shop-groups", json={"name": f"GROUP {n + 1}", "connection_ids": members})
        groups.append(next(g["id"] for g in res.json()["groups"] if g["name"] == f"GROUP {n + 1}"))
    content = await _timed("review page: 500 listings", a.get(f"/api/batches/{batch}/content"), 60)
    assert content.status_code == 200 and len(content.json()) == LISTINGS
    await _timed("batches with statuses", a.get("/api/batches"), 20)

    await a.put(f"/api/batches/{batch}/send-to", json={"send_to": {"connection_ids": shops}})
    preview = await _timed("Where and when preview: 500 x 15", a.post(f"/api/batches/{batch}/send/preview", json={"when": "drafts"}), 120)
    assert preview.json()["total"] == LISTINGS * SHOPS
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "per_shop_per_day": 50,
                "window_start": "06:00", "window_end": "23:00", "spacing_minutes": 5, "stagger_minutes": 1}
    await _timed("spread out preview: 7,500 go-lives", a.post(f"/api/batches/{batch}/send/preview",
                                                             json={"when": "spread", "schedule": schedule}), 180)
    sent = await _timed("send spread out: 7,500 planned", a.post(f"/api/batches/{batch}/send", json={"when": "spread", "schedule": schedule}), 240)
    assert sent.status_code == 200 and sent.json()["scheduled"] == LISTINGS * SHOPS
    data = await _timed("Scheduled page: 7,500 go-lives", a.get("/api/scheduled"), 120)
    keys = [i["key"] for i in data.json()["items"]][:LISTINGS]
    shifted = await _timed("bulk shift preview: 500", a.post("/api/scheduled/preview",
                                                             json={"op": "shift", "keys": keys, "params": {"minutes": 60}}), 120)
    assert len(shifted.json()["rows"]) == LISTINGS
    await w["engine"].dispose()


async def test_a_two_hundred_photo_upload(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    a = w["client"]
    storage = LocalStorage(tmp_path / "storage")
    a._transport.app.dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(  # type: ignore[attr-defined]
        sessionmaker=w["sm"], storage=storage, processor=ImageProcessor(), sku_parser=SkuParser())
    a._transport.app.dependency_overrides[deps.get_storage] = lambda: storage  # type: ignore[attr-defined]
    batch = (await a.post("/api/batches")).json()["id"]
    photo = _jpeg(1)

    async def upload() -> None:
        for n in range(200):
            res = await a.post(f"/api/batches/{batch}/assets", files={"file": (f"BR{n // 4:03d}-{n % 4}.jpg", photo, "image/jpeg")},
                               data={"grouping": "sku"})
            assert res.status_code == 201, res.text

    await _timed("200-photo upload (2400x3000 JPEG each)", upload(), 600)
    summary = (await a.get(f"/api/batches/{batch}")).json()
    assert summary["file_count"] == 200 or summary.get("processed_count", 200) == 200
    groups = (await a.get(f"/api/batches/{batch}/groups")).json()
    assert len(groups) == 50
    await w["engine"].dispose()
