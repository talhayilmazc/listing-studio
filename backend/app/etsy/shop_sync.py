"""Whether a shop's listing sync is running, and when one last ended (fix/onboarding-followups, Part I).

Before: each page guessed. Profiles looked again at 5 and 15 seconds after "Sync shop listings" and
called the shop synced as soon as its six-hour copy was not stale (the old copy already was not); a
sync that took longer than that never showed. A shop with no listings had no copy at all, so it was
"stale" forever and its listings said "0 · syncing…" for good, asking for a sync on every look.

Now the worker says it (``sync_shop_listings``): "running" while the job runs, "ended" and, when it
read the shop, "synced" when it stops, whatever happened. The API adds a short "requested" mark where it
queues a sync, so a page says "syncing" while the job waits for a worker; it runs out by itself if no
job runs (one was already queued or just finished). ``GET /api/shop/sync`` gives every shop's state;
the app's one watcher (frontend/lib/shopSync.ts) follows it while a shop syncs and tells every page
that shows the shop's listings when the sync ends.

Redis only (the account's own work state, not Etsy content); the times only, nothing from the shop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: A queued sync waits for a worker at most about this long before "syncing" stops being said.
REQUESTED_SECONDS = 90
#: A worker that dies mid-sync stops saying "running" after this long (a full read takes a minute or two).
RUNNING_SECONDS = 15 * 60
#: How long "synced" stands in for the copy's age: the copy's own lifetime (six hours).
SYNCED_SECONDS = 6 * 3600


def _keys(connection_id: uuid.UUID | str) -> tuple[str, str, str, str]:
    c = str(connection_id)
    return f"shop-sync:{c}", f"shop-sync-asked:{c}", f"shop-sync-ended:{c}", f"shop-synced:{c}"


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def requested(redis: Any, connection_id: uuid.UUID | str) -> None:
    """The API queued a sync: say "syncing" until the worker takes it (or for a short while)."""
    _, asked, _, _ = _keys(connection_id)
    await redis.set(asked, _now(), ex=REQUESTED_SECONDS)


async def started(redis: Any, connection_id: uuid.UUID | str) -> None:
    running, asked, _, _ = _keys(connection_id)
    await redis.set(running, _now(), ex=RUNNING_SECONDS)
    await redis.delete(asked)


async def ended(redis: Any, connection_id: uuid.UUID | str, *, read: bool) -> None:
    """The job stopped. ``read``: it read the shop (an empty shop too), so its copy is current."""
    running, asked, done, synced = _keys(connection_id)
    now = _now()
    await redis.delete(running, asked)
    await redis.set(done, now, ex=SYNCED_SECONDS)
    if read:
        await redis.set(synced, now, ex=SYNCED_SECONDS)


@dataclass(frozen=True)
class SyncState:
    syncing: bool
    ended_at: datetime | None
    synced_at: datetime | None


def _at(raw: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


async def state(redis: Any, connection_id: uuid.UUID | str) -> SyncState:
    running, asked, done, synced = await redis.mget(list(_keys(connection_id)))
    return SyncState(syncing=running is not None or asked is not None, ended_at=_at(done), synced_at=_at(synced))
