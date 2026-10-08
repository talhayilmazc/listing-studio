"""Once a day: delete image files that are past their time, then tell the
operator what was freed and what is using the disk.

The summary is one message to ``ALERT_WEBHOOK_URL`` (core/alerts.py): counts and
sizes only, nothing of any seller's. It is sent whether or not anything was
deleted, so a day without one means the job did not run.

Runs at most once per UTC day, however often the worker restarts: the cron
entry also runs at startup so a worker that was down at the hour catches up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.api.deps import get_storage
from app.core import alerts, disk
from app.core.config import get_settings
from app.pipeline import upload_retention

logger = logging.getLogger(__name__)

_DONE = "upkeep:done:{day}"


def summary(
    result: upload_retention.Result, snapshot: disk.Snapshot, day: str, growth: disk.Forecast | None = None
) -> str:
    """The daily message. Plain text: it is read in a notification."""
    pace = ""
    if growth is not None and growth.days_left is not None:
        pace = f" At the last {growth.basis_days} days' rate ({disk.size(int(growth.per_day or 0))} a day) it is full in {growth.days_left:.0f} days."
    verb = "freed" if result.applied else "would free (dry run, nothing was deleted)"
    if result.groups:
        parts = []
        if result.drafted_groups:
            parts.append(f"{result.drafted_groups} drafted in every shop {result.drafted_days}+ days ago")
        if result.unpublished_groups:
            parts.append(f"{result.unpublished_groups} not worked on for {result.unpublished_days} days")
        cleanup = (
            f"Upload cleanup {verb} {disk.size(result.freed_bytes)}: {result.files} files of "
            f"{result.groups} listings ({', '.join(parts)})."
        )
    else:
        cleanup = "Upload cleanup: nothing was due."
    if result.waiting:
        cleanup += f" {result.waiting} more wait for something pending (a schedule, a distribution, a draft or replace)."
    if snapshot.free_bytes is not None and snapshot.total_bytes is not None:
        space = f"Disk: {disk.size(snapshot.free_bytes)} free of {disk.size(snapshot.total_bytes)}."
    else:
        space = "Disk: free space unknown."
    use = ", ".join(f"{disk.LABELS[c.key].lower()} {disk.size(c.bytes)}" for c in snapshot.categories)
    stale = "" if snapshot.host_fresh else " (Backups and Docker are measured by deploy/disk-check.sh, which has not reported lately.)"
    return f"Listyro daily summary, {day}. {cleanup} {space}{pace} In use: {use}.{stale}"


async def daily_upkeep(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """arq cron entry point."""
    redis = ctx["redis"]
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    # One run a day: the first worker to ask does it.
    if not await redis.set(_DONE.format(day=day), "1", nx=True, ex=36 * 3600):
        return None
    storage = ctx.get("storage") or get_storage()
    try:
        async with ctx["sessionmaker"]() as session:
            result = await upload_retention.run(session, storage, now=now, apply=get_settings().upload_retention_apply)
            await upload_retention.remember(session, result)
            snapshot = await disk.snapshot(session, storage, redis, fresh=True)
            # One reading a day: the "days until the disk is full" rate (Admin > Usage).
            growth = disk.forecast(await disk.record_history(session, snapshot, day), snapshot.free_bytes)
    except Exception:
        # Let a later start today try again, and say that today's did not finish.
        await redis.delete(_DONE.format(day=day))
        await alerts.send(f"Listyro daily summary, {day}: the upload cleanup failed; see the worker log.")
        raise
    await alerts.send(summary(result, snapshot, day, growth))
    return result.as_dict()
