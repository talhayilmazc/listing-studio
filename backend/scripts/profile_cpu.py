"""Where one request spends its CPU (dev aid for the load test; scratch database only).

    docker compose run --rm --entrypoint python tests scripts/profile_cpu.py
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import scripts.load_test as lt  # noqa: E402
from app.core.config import get_settings, set_settings_override  # noqa: E402


async def main() -> None:
    url = lt._fresh_database()
    set_settings_override(get_settings().model_copy(update={"database_url": url, "rate_limit_requests": 10**9}))
    engine = create_async_engine(url)
    worlds, _, _ = await lt.seed(engine, 1, 3, 500)
    clients = await lt._app_clients(engine, worlds)
    w = worlds[0]
    b, s = w["batches"][0], w["shops"][1]
    for url_ in ["/api/batches", f"/api/batches/{b}/content", f"/api/batches/{b}/shops/{s}/rows", "/api/attention",
                 f"/api/batches/{b}", "/api/drafts/unfinished"]:
        await clients[0].get(url_)
        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            await clients[0].get(url_)
        prof.disable()
        out = io.StringIO()
        stats = pstats.Stats(prof, stream=out).sort_stats("cumulative")
        stats.sort_stats("tottime").print_stats(12)
        text = out.getvalue()
        print(f"\n===== {url_.split('/api/')[1][:50]}  total {stats.total_tt / 3 * 1000:.0f} ms CPU per request")
        print(text[-3000:])
    await engine.dispose()
    lt._drop_database()


asyncio.run(main())
