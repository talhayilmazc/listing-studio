"""Writing a batch's listings in the worker (fix/onboarding-followups, Part D).

Before: "Write listings" ran in the browser, one request per group. Closing the tab stopped it after the
group in flight; the rest were never written (tests/test_writing_job.py shows both). Now the page asks
once (``POST /api/batches/{id}/write``) and this job writes every group that still needs it:

* it keeps going whatever the browser does;
* it is safe to run again: each group goes through the same ``_write_listings`` as before, which skips a
  group that already has a listing and records the allowance only for a listing it wrote; one job per
  batch (arq job id), one writing run per account at a time (the same ``generating:{tenant}`` lock);
* the cost guards are the same code: the allowance check, the AI provider pause (the run stops and says
  why), the metering scope per group;
* progress lives on the server (``writing:{batch}``: total, done, written, failed, each failure's file and
  reason, state), so any page can show "Writing 3 of 12";
* when it ends, a note waits for the seller (``writing-ready:{tenant}``): "Listings ready" on whatever
  page they open next, until they have seen it.

It makes no Etsy call: a profile that needs reading again is queued as its own job, as before.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from arq import Retry

from app.core.config import get_settings

#: How long progress and the "Listings ready" note are kept (not Etsy content; the account's own work).
KEEP_SECONDS = 7 * 24 * 3600
#: Another writing request of this account holds the lock: try again this much later.
BUSY_RETRY_SECONDS = 20
LOCK_SECONDS = 600


def progress_key(batch_id: uuid.UUID | str) -> str:
    return f"writing:{batch_id}"


def ready_key(tenant_id: uuid.UUID | str) -> str:
    return f"writing-ready:{tenant_id}"


def job_id(batch_id: uuid.UUID | str) -> str:
    return f"write:{batch_id}"


async def read(redis: Any, batch_id: uuid.UUID | str) -> dict[str, Any] | None:
    raw = await redis.get(progress_key(batch_id))
    return json.loads(raw) if raw else None


async def _save(redis: Any, batch_id: uuid.UUID | str, state: dict[str, Any]) -> None:
    await redis.set(progress_key(batch_id), json.dumps(state), ex=KEEP_SECONDS)


async def start(redis: Any, enqueue: Any, *, tenant_id: uuid.UUID, batch_id: uuid.UUID, groups: list[str],
                profile_id: uuid.UUID | None, ignore_unsorted: bool, name: str,
                options: dict[str, bool] | None = None) -> dict[str, Any]:
    """Record what is to be written and queue the job. A run already going for this batch is returned as it is."""
    now = await read(redis, batch_id)
    if now and now.get("state") in ("queued", "running"):
        return now
    state = {
        "tenant_id": str(tenant_id), "batch_id": str(batch_id), "name": name, "groups": groups, "finished": [],
        "total": len(groups), "done": 0, "written": 0, "failed": 0, "skipped": 0, "failures": [],
        "state": "queued", "message": None, "started_at": time.time(),
        "profile_id": str(profile_id) if profile_id else None, "ignore_unsorted": ignore_unsorted,
        # Generate's choices (Regenerate, "Generate the empty ones"): each group is written with them.
        "options": {k: bool(v) for k, v in (options or {}).items() if k in ("replace", "replace_approved", "only_empty")},
        "skipped_groups": [],
    }
    await _save(redis, batch_id, state)
    await enqueue("write_batch_listings", str(batch_id), _job_id=job_id(batch_id))
    return state


async def take_ready(redis: Any, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """The finished runs the seller has not been told about yet; told now, so they are cleared."""
    key = ready_key(tenant_id)
    notes = [json.loads(x) for x in await redis.lrange(key, 0, -1)]
    if notes:
        await redis.delete(key)
    return notes


async def write_batch_listings(ctx: dict[str, Any], batch_id: str) -> str:
    """Write every group of the batch that still needs a listing (see the module's notes)."""
    from app.api import schemas
    from app.api.batches import _write_listings
    from app.api.deps import Enqueuer
    from fastapi import HTTPException

    from app.db.models import Tenant, UploadBatch
    from app.pipeline.storage import LocalStorage

    redis = ctx["redis"]
    state = await read(redis, batch_id)
    if not state or state.get("state") in ("done", "paused"):
        return "nothing"
    tenant_id = uuid.UUID(state["tenant_id"])
    lock = f"generating:{tenant_id}"
    if not await redis.set(lock, "job", nx=True, ex=LOCK_SECONDS):
        raise Retry(defer=BUSY_RETRY_SECONDS)  # a writing request of another tab: after it
    settings = get_settings()
    storage = ctx.get("storage") or LocalStorage(settings.storage_dir)
    enqueuer = ctx.get("enqueuer") or Enqueuer(settings.redis_url)
    try:
        state["state"] = "running"
        await _save(redis, batch_id, state)
        for key in [g for g in state["groups"] if g not in state["finished"]]:
            await redis.expire(lock, LOCK_SECONDS)
            body = schemas.GenerateRequest(
                group_key=key, profile_id=uuid.UUID(state["profile_id"]) if state["profile_id"] else None,
                ignore_unsorted=state["ignore_unsorted"], **state.get("options", {}),
            )
            async with ctx["sessionmaker"]() as session:
                tenant = await session.get(Tenant, tenant_id)
                batch = await session.get(UploadBatch, uuid.UUID(batch_id))
                if tenant is None or batch is None or batch.deleted_at is not None or tenant.deletion_requested_at:
                    state.update(state="done", message="This batch is no longer there.")
                    break
                try:
                    result = await _write_listings(uuid.UUID(batch_id), body, session, tenant, storage, enqueuer, redis)
                except HTTPException as exc:
                    # The allowance ran out between two groups: the rest wait, nothing failed.
                    state.update(state="paused", message=str(exc.detail))
                    break
            if result.paused:
                # Our AI provider refused us (usage, credit): nothing failed; the rest wait for "Write listings" again.
                state.update(state="paused", message=result.paused)
                break
            state["finished"].append(key)
            state["done"] += 1
            state["written"] += result.generated
            state["failed"] += result.failed
            state["skipped"] += result.skipped
            state["failures"] += [{"file": f.original_filename, "error": f.error} for f in result.failures]
            state.setdefault("skipped_groups", []).extend(g.model_dump() for g in result.skipped_groups)
            await _save(redis, batch_id, state)
        else:
            state["state"] = "done"
        await _save(redis, batch_id, state)
        if state["state"] == "done":
            note = {"batch_id": batch_id, "name": state["name"], "written": state["written"], "failed": state["failed"]}
            await redis.rpush(ready_key(tenant_id), json.dumps(note))
            await redis.expire(ready_key(tenant_id), KEEP_SECONDS)
        return state["state"]
    finally:
        await redis.delete(lock)
