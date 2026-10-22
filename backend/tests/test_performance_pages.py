"""The main pages at volume (quality pass 2026-10, docs/qa-2026-10.md section 7).

A seller with 15 shops and 500 listings spread over 100 batches, 300 of them drafted in three
shops each. Every main page's API answer is timed and its SQL queries counted; any single query
over 100 ms is printed. The bounds: under 500 ms per page here (SQLite, in process; production's
Postgres is faster on these), and a query count that does not grow with the number of batches
(review R11: the batch list ran ~12-15 queries per batch). ``-s`` prints the table.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, update

from app.core.config import Settings
from app.db.models import Asset, GeneratedContent, ListingPublication, UploadBatch, UploadBatchStatus
from tests.test_end_to_end import _world

SHOPS, LISTINGS, BATCHES, DRAFTED = 15, 500, 100, 300
#: Per page: time bound (s) and the most SQL queries one answer may take.
PAGES = {
    "/api/batches": (0.5, 45),  # 1,923 queries before the bulk summary (19 per batch)
    "/api/shop/overview": (0.5, 60),
    "/api/shop/summary?all=1": (0.5, 40),
    "/api/attention": (0.5, 40),
    "/api/profiles": (0.5, 40),
    "/api/scheduled": (0.5, 40),
    "/api/finish-in-etsy": (0.5, 20),
    "/api/quota": (0.5, 20),
    "/api/account/allowance": (0.5, 20),
    "/api/drafts/unfinished": (0.5, 40),
    "/api/shops": (0.5, 30),
    "/api/shop-groups": (0.5, 20),
    "/api/schedules": (0.5, 20),
    # Each shop's own month report, reused: ~14 queries per shop (15 shops: 212, ~0.2 s).
    "/api/analytics/shops": (0.5, 15 * SHOPS),
    "/api/auth/etsy/status": (0.5, 20),
}
SLOW_QUERY = 0.1


@pytest.mark.parametrize("_", [None])
async def test_main_pages_with_15_shops_and_500_listings(_, tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=SHOPS, listings=LISTINGS)
    a, sm, engine = w["client"], w["sm"], w["engine"]
    async with sm() as s:  # spread the listings over 100 batches, and draft 300 of them in 3 shops
        assets = (await s.execute(Asset.__table__.select().where(Asset.batch_id == w["batch"]))).all()
        batches = [w["batch"]]
        for n in range(BATCHES - 1):
            b = UploadBatch(tenant_id=w["tenant"], status=UploadBatchStatus.ready, file_count=5, name=f"Batch {n:03d}")
            s.add(b)
            await s.flush()
            batches.append(b.id)
        for i, row in enumerate(assets):
            target = batches[i % BATCHES]
            await s.execute(update(Asset).where(Asset.id == row.id).values(batch_id=target))
            await s.execute(update(GeneratedContent).where(GeneratedContent.asset_id == row.id).values(batch_id=target))
        for i, content_id in enumerate(w["contents"][:DRAFTED]):
            for k in range(3):
                s.add(ListingPublication(tenant_id=w["tenant"], content_id=content_id, connection_id=w["shops"][(i + k) % SHOPS],
                                         profile_id=w["profile"], etsy_listing_id=10_000 + i * 3 + k,
                                         state="active" if k == 0 else "draft", published_at=datetime.now(timezone.utc)))
        await s.commit()

    counted: dict[str, int] = {"n": 0}
    slow: list[str] = []
    starts: dict[int, float] = {}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        counted["n"] += 1
        starts[id(cursor)] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        took = time.perf_counter() - starts.pop(id(cursor), time.perf_counter())
        if took > SLOW_QUERY:
            slow.append(f"{took * 1000:.0f} ms: {' '.join(statement.split())[:160]}")

    rows, over = [], []
    paths = {**PAGES, f"/api/batches/{batches[1]}/content": (0.5, 60), f"/api/batches/{batches[1]}": (0.5, 50)}
    for path, (bound, max_queries) in paths.items():
        await a.get(path)  # warm (first-call imports, caches)
        counted["n"] = 0
        start = time.perf_counter()
        res = await a.get(path)
        took = time.perf_counter() - start
        assert res.status_code == 200, (path, res.status_code, res.text[:200])
        rows.append(f"{path:<48} {took * 1000:6.0f} ms {counted['n']:5d} queries")
        if took > bound or counted["n"] > max_queries:
            over.append(f"{path}: {took * 1000:.0f} ms, {counted['n']} queries (bounds {bound * 1000:.0f} ms, {max_queries})")
    print("\n" + "\n".join(rows))
    if slow:
        print("queries over 100 ms:\n" + "\n".join(slow))
    await engine.dispose()
    assert not over, "\n".join(over)
    assert not slow, "\n".join(slow)
    assert uuid  # (kept for ids in failure messages)
