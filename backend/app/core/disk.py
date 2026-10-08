"""What is using the server's disk, by category.

Two sources, because the app's containers cannot see the whole machine:

  * the app measures what it holds itself: stored files, split into uploads (the
    originals) and derivatives (processed copies, previews, kept thumbnails),
    and the database's size. Walking the storage takes a moment, so the figures
    are kept for ten minutes.
  * the host measures the rest: backups and Docker (images, build cache,
    container layers and logs; not the volumes, which are the two above).
    ``deploy/disk-check.sh`` already runs hourly from cron and leaves its figures
    in Redis under ``ops:disk``. Until it has run, those categories are unknown,
    and are reported as unknown rather than as zero.

Total and free space are read where the uploads are stored, which on the
production server is the one disk everything shares.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.pipeline.storage import Storage

logger = logging.getLogger(__name__)

#: Written by deploy/disk-check.sh (hourly): {"at", "backups_bytes", "docker_bytes"}.
HOST_KEY = "ops:disk"
#: The host's figures are trusted for this long; older, they are shown as stale.
HOST_FRESH_SECONDS = 3 * 3600
APP_KEY = "disk:app"
APP_CACHE_SECONDS = 600

LABELS = {
    "uploads": "Uploads",
    "derivatives": "Derivatives",
    "database": "Database",
    "backups": "Backups",
    "docker": "Docker",
    "other": "System and other",
}
NOTES = {
    "uploads": "The original image files sellers uploaded",
    "derivatives": "Processed copies, previews and kept cover thumbnails",
    "database": "PostgreSQL's size for this database",
    "backups": "Database dumps and the weekly storage archive on this server",
    "docker": "Images, build cache, container layers and logs (not the volumes)",
    "other": "The operating system and everything not counted above",
}


@dataclass
class Category:
    key: str
    bytes: int | None  # None: not known (the host has not reported)
    files: int | None = None


@dataclass
class Snapshot:
    at: float
    total_bytes: int | None
    free_bytes: int | None
    categories: list[Category]
    #: When the host last reported (epoch seconds), and whether that is recent.
    host_at: float | None
    host_fresh: bool
    #: Stored bytes per account (its id), from the same measurement.
    accounts: dict[str, int] | None = None

    def get(self, key: str) -> int | None:
        return next((c.bytes for c in self.categories if c.key == key), None)


def _device(storage_dir: str) -> tuple[int | None, int | None]:
    try:
        usage = shutil.disk_usage(storage_dir)
    except OSError:
        return None, None
    return usage.total, usage.free


async def _database_bytes(session: AsyncSession) -> int | None:
    if session.get_bind().dialect.name != "postgresql":
        return None
    try:
        return int((await session.execute(text("SELECT pg_database_size(current_database())"))).scalar() or 0)
    except Exception:  # noqa: BLE001 - a size we cannot read is unknown, not an error page
        logger.exception("could not read the database size")
        return None


async def app_usage(session: AsyncSession, storage: Storage, redis: Redis, *, fresh: bool = False) -> dict[str, Any]:
    """What the app itself holds; measured at most once in ten minutes."""
    if not fresh:
        cached = await redis.get(APP_KEY)
        if cached:
            try:
                return json.loads(cached)
            except ValueError:
                pass
    measured: dict[str, Any] = await asyncio.to_thread(storage.usage)
    measured["accounts"] = await asyncio.to_thread(_by_account, storage)
    measured["database_bytes"] = await _database_bytes(session)
    measured["at"] = time.time()
    await redis.set(APP_KEY, json.dumps(measured), ex=APP_CACHE_SECONDS)
    return measured


def _by_account(storage: Storage) -> dict[str, int]:
    """Stored bytes per account folder (the account's id). Sizes only."""
    files = getattr(storage, "files", None)
    out: dict[str, int] = {}
    if files is None:
        return out
    for key, size_, _ in files():
        account = key.split("/", 1)[0]
        out[account] = out.get(account, 0) + size_
    return out


# --- How fast the disk fills ------------------------------------------------------------
#: app_setting: one reading a day, newest last: {"day", "used", "free", "storage"}.
HISTORY_KEY = "disk_history"
HISTORY_DAYS = 30
#: The rate is the average over at most this many days of readings.
RATE_DAYS = 7


async def record_history(session: AsyncSession, snap: "Snapshot", day: str) -> list[dict[str, Any]]:
    """Keep today's reading (the daily job calls this); one per day, 30 days."""
    from app.db.models import AppSetting

    if snap.total_bytes is None or snap.free_bytes is None:
        return []
    storage = sum(c.bytes or 0 for c in snap.categories if c.key in ("uploads", "derivatives"))
    row = await session.get(AppSetting, HISTORY_KEY)
    history = [h for h in (row.value if row is not None and isinstance(row.value, list) else []) if h.get("day") != day]
    history.append({"day": day, "used": snap.total_bytes - snap.free_bytes, "free": snap.free_bytes, "storage": storage})
    history = sorted(history, key=lambda h: h["day"])[-HISTORY_DAYS:]
    if row is None:
        session.add(AppSetting(key=HISTORY_KEY, value=history))
    else:
        row.value = history
    await session.commit()
    return history


async def history(session: AsyncSession) -> list[dict[str, Any]]:
    from app.db.models import AppSetting

    row = await session.get(AppSetting, HISTORY_KEY)
    return list(row.value) if row is not None and isinstance(row.value, list) else []


@dataclass
class Forecast:
    #: Bytes a day the disk's used space grew, on average; None: fewer than two readings.
    per_day: float | None
    #: Days until no space is left at that rate; None: not growing, or not known.
    days_left: float | None
    #: Days the average covers.
    basis_days: int
    storage_per_day: float | None = None


def forecast(readings: list[dict[str, Any]], free_now: int | None) -> Forecast:
    """At the current rate (the last :data:`RATE_DAYS` days of daily readings),
    how long until the disk is full."""
    from datetime import date

    recent = sorted(readings, key=lambda h: h["day"])[-(RATE_DAYS + 1):]
    if len(recent) < 2:
        return Forecast(None, None, 0)
    first, last = recent[0], recent[-1]
    days = (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
    if days <= 0:
        return Forecast(None, None, 0)
    per_day = (last["used"] - first["used"]) / days
    storage_per_day = (last.get("storage", 0) - first.get("storage", 0)) / days
    free = free_now if free_now is not None else last.get("free")
    days_left = (free / per_day) if per_day > 0 and free is not None else None
    return Forecast(per_day, days_left, days, storage_per_day)


async def host_report(redis: Redis) -> dict[str, Any] | None:
    raw = await redis.get(HOST_KEY)
    if not raw:
        return None
    try:
        report = json.loads(raw)
    except ValueError:
        return None
    return report if isinstance(report, dict) else None


def _whole(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


async def snapshot(session: AsyncSession, storage: Storage, redis: Redis, *, fresh: bool = False) -> Snapshot:
    app = await app_usage(session, storage, redis, fresh=fresh)
    host = await host_report(redis) or {}
    total, free = await asyncio.to_thread(_device, get_settings().storage_dir)
    host_at = host.get("at") if isinstance(host.get("at"), (int, float)) else None
    categories = [
        Category("uploads", _whole(app.get("uploads_bytes")), _whole(app.get("uploads_files"))),
        Category("derivatives", _whole(app.get("derivatives_bytes")), _whole(app.get("derivatives_files"))),
        Category("database", _whole(app.get("database_bytes"))),
        Category("backups", _whole(host.get("backups_bytes"))),
        Category("docker", _whole(host.get("docker_bytes"))),
    ]
    # What is left over is only meaningful when every category is known.
    other = None
    if total is not None and free is not None and all(c.bytes is not None for c in categories):
        other = max(0, total - free - sum(c.bytes or 0 for c in categories))
    categories.append(Category("other", other))
    return Snapshot(
        at=time.time(), total_bytes=total, free_bytes=free, categories=categories, host_at=host_at,
        host_fresh=host_at is not None and time.time() - host_at < HOST_FRESH_SECONDS,
        accounts={str(k): int(v) for k, v in (app.get("accounts") or {}).items()},
    )


def size(value: int | None) -> str:
    """Bytes as a person reads them: "412 MB", "6.1 GB"; unknown is "unknown"."""
    if value is None:
        return "unknown"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.0f} MB"
    return f"{value / 1024:.0f} KB"
