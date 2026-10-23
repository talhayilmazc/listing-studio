"""``python -m app.cli thumbnails [--apply] [--email <address>] [--keep-free-gb N]``: the small
copies screens use (pipeline/thumbs.py) for images stored before they existed, and the
previews of the earlier scheme removed.

Made ahead (the rest are made the first time a screen asks):

* batch photos (and a removed group's kept cover): ``small`` and ``thumb``;
* template photos: ``small``, ``thumb`` and ``medium`` (the editor);
* mockups: ``small`` (the results grid).

Earlier previews (``<key>.v2.w224.jpg``, ``<key>.w224.jpg``) are asked for by nothing after
this update, so ``--apply`` deletes them: usually that frees more than the copies take.

Within the disk and storage budget: before each account it measures what is left. It
stops when the disk would have less than ``--keep-free-gb`` (default 5 GB) free, and leaves
out an account whose copies would take it over its storage cap (each is listed; its screens
still make what they ask for). A dry run unless ``--apply``; a dry run makes a sample of
copies in memory only, to say how much the rest will take. Read-only for the database.
"""

from __future__ import annotations

import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import storage_cap
from app.db.models import Asset, MockupOutput, MockupPhoto, Tenant
from app.pipeline import thumbs
from app.pipeline.images import ImageProcessingError
from app.pipeline.storage import LEGACY, Storage

#: What is made ahead for each kind of stored image.
AHEAD = {"photo": ("small", "thumb"), "template": ("small", "thumb", "medium"), "mockup": ("small",)}
SAMPLE = 12  # copies made in memory by a dry run, to estimate the rest


@dataclass
class Job:
    tenant_id: uuid.UUID
    kind: str
    key: str


@dataclass
class Tally:
    made: int = 0
    made_bytes: int = 0
    missing: int = 0
    failed: int = 0
    by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _mb(n: float) -> str:
    return f"{n / 1e6:,.1f} MB"


async def _jobs(sm: async_sessionmaker, email: str | None) -> tuple[list[Job], dict[uuid.UUID, Tenant]] | str:
    async with sm() as session:
        tenants = {t.id: t for t in (await session.execute(select(Tenant))).scalars()}
        only: uuid.UUID | None = None
        if email:
            match = [t for t in tenants.values() if t.email == email.strip().lower()]
            if not match:
                return f"no account with the address {email}"
            only = match[0].id
        jobs: list[Job] = []
        for a in (await session.execute(select(Asset.tenant_id, Asset.processed_key, Asset.thumbnail_key, Asset.files_removed_at))).all():
            key = a.thumbnail_key if a.files_removed_at is not None else a.processed_key
            if key and (only is None or a.tenant_id == only):
                jobs.append(Job(a.tenant_id, "photo", key))
        for p in (await session.execute(select(MockupPhoto.tenant_id, MockupPhoto.image_key).where(
                MockupPhoto.status == "ready", MockupPhoto.image_key.is_not(None)))).all():
            if only is None or p.tenant_id == only:
                jobs.append(Job(p.tenant_id, "template", p.image_key))
        for o in (await session.execute(select(MockupOutput.tenant_id, MockupOutput.key).where(
                MockupOutput.key.is_not(None), MockupOutput.files_removed_at.is_(None)))).all():
            if only is None or o.tenant_id == only:
                jobs.append(Job(o.tenant_id, "mockup", o.key))
        return jobs, ({only: tenants[only]} if only else tenants)


def _legacy(storage: Storage, tenants: dict[uuid.UUID, Tenant]) -> tuple[list[str], int]:
    files = getattr(storage, "files", None)
    if files is None:
        return [], 0
    found, size = [], 0
    for tenant_id in tenants:
        for key, n, _ in files(str(tenant_id)):
            if LEGACY.search(key):
                found.append(key)
                size += n
    return found, size


def _free(storage: Storage) -> int | None:
    base = getattr(storage, "_base", None)
    if base is None:
        return None
    try:
        return shutil.disk_usage(base).free
    except OSError:
        return None


async def run(sm: async_sessionmaker, storage: Storage, *, apply: bool, email: str | None = None,
              keep_free_gb: float = 5.0) -> str:
    found = await _jobs(sm, email)
    if isinstance(found, str):
        return found
    jobs, tenants = found
    todo = [(j, size) for j in jobs for size in AHEAD[j.kind] if storage.exists(j.key)
            and not storage.exists(thumbs.key_for(j.key, size))]
    legacy, legacy_bytes = _legacy(storage, tenants)
    lines = [f"{len(jobs)} stored image(s); {len(todo)} cop(ies) to make "
             + "(" + ", ".join(f"{k}: {n}" for k, n in sorted(_count(todo).items())) + ")" if todo else
             f"{len(jobs)} stored image(s); every copy is already made"]
    lines.append(f"{len(legacy)} earlier preview(s), {_mb(legacy_bytes)}, to delete")

    if not apply:
        sample = todo[::max(1, len(todo) // SAMPLE)][:SAMPLE]  # across the kinds, not the first ones
        made = []
        for job, size in sample:
            try:
                made.append(len(thumbs.make(storage.get(job.key), size)))
            except (FileNotFoundError, ImageProcessingError):
                continue
        if made and todo:
            estimate = sum(made) / len(made) * len(todo)
            lines.append(f"about {_mb(estimate)} for the copies (from {len(made)} made in memory); "
                         f"net {_mb(estimate - legacy_bytes)}")
        free = _free(storage)
        if free is not None:
            lines.append(f"disk free now: {_mb(free)}; it stops below {keep_free_gb:g} GB free")
        lines.append("dry run: nothing written or deleted; run with --apply")
        return "\n".join(lines)

    # The earlier previews first: they free room for the copies.
    freed = 0
    for key in legacy:
        freed += storage.delete_with_derivatives(key)
    reserve = int(keep_free_gb * 1e9)
    tally = Tally()
    skipped: list[str] = []
    stopped = False
    by_tenant: dict[uuid.UUID, list[tuple[Job, str]]] = defaultdict(list)
    for job, size in todo:
        by_tenant[job.tenant_id].append((job, size))
    for tenant_id, items in by_tenant.items():
        tenant = tenants.get(tenant_id)
        free = _free(storage)
        if free is not None and free < reserve:
            stopped = True
            break
        if tenant is not None:
            used = storage_cap.used(storage, tenant_id, fresh=True)
            if used + len(items) * 60_000 > storage_cap.limit(tenant):  # ~60 KB a copy, a cautious average
                skipped.append(f"{tenant.email}: {_mb(used)} used of {_mb(storage_cap.limit(tenant))}")
                continue
        for job, size in items:
            try:
                data = thumbs.make(storage.get(job.key), size)
            except FileNotFoundError:
                tally.missing += 1
                continue
            except ImageProcessingError:
                tally.failed += 1
                continue
            storage.put(thumbs.key_for(job.key, size), data, "image/webp")
            tally.made += 1
            tally.made_bytes += len(data)
            tally.by_kind[job.kind] += 1
        storage_cap.forget(tenant_id)
    lines = [f"made {tally.made} cop(ies), {_mb(tally.made_bytes)}"
             + (" (" + ", ".join(f"{k}: {n}" for k, n in sorted(tally.by_kind.items())) + ")" if tally.by_kind else ""),
             f"deleted {len(legacy)} earlier preview(s), {_mb(freed)}"]
    if tally.missing or tally.failed:
        lines.append(f"{tally.missing} source file(s) gone, {tally.failed} could not be read")
    if skipped:
        lines.append(f"left out {len(skipped)} account(s) at their storage cap (their screens make copies when asked):")
        lines += [f"  {s}" for s in skipped]
    if stopped:
        lines.append(f"stopped: less than {keep_free_gb:g} GB free on the disk; run it again after making room")
    return "\n".join(lines)


def _count(todo: list[tuple[Job, str]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for job, size in todo:
        out[f"{job.kind} {size}"] += 1
    return out

