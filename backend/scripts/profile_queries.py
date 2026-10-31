"""Which SQL statements one request runs, grouped (dev aid for the load test; scratch database only).

    docker compose run --rm --entrypoint python tests scripts/profile_queries.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import scripts.load_test as lt  # noqa: E402
from app.core.config import get_settings, set_settings_override  # noqa: E402


async def main() -> None:
    url = lt._fresh_database()
    set_settings_override(get_settings().model_copy(update={"database_url": url, "rate_limit_requests": 10**9}))
    engine = create_async_engine(url)
    worlds, _, _ = await lt.seed(engine, 1, 3, 500)
    seen: Counter[str] = Counter()
    loaded: Counter[str] = Counter()

    @event.listens_for(Session, "loaded_as_persistent")
    def _loaded(_session, instance):  # noqa: ANN001
        loaded[type(instance).__name__] += 1

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _log(conn, cursor, statement, *_):  # noqa: ANN001
        if lt.current_request.get() is not None:
            s = re.sub(r"\$\d+(::\w+(\[\])?)?", "?", " ".join(statement.split()))
            s = re.sub(r"SELECT .*? FROM", "SELECT … FROM", s)
            seen[s[:170]] += 1

    clients = await lt._app_clients(engine, worlds)
    for page, urls in lt.pages_for(worlds[0]).items():
        for u in urls:
            seen.clear()
            loaded.clear()
            ms, status, q = await lt._get(clients[0], u)
            q = sum(seen.values())
            if q > 20:
                print(f"\n{u.split('/api/')[1][:60]}  {ms:.0f} ms  {q} queries  status {status}")
                for s, n in seen.most_common(8):
                    print(f"  {n:4} × {s}")
    await engine.dispose()
    lt._drop_database()


asyncio.run(main())
