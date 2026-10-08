"""Per-account cap on stored image files (default 5 GB; an admin can override).

The production disk cannot grow, and one account once held 10.9 GB of a 30 GB
disk. An upload that would take an account past its cap is refused before it
is stored, with how much it uses and what frees space.

What counts: every file under the account's folder in storage (processed
copies, originals still kept from before, previews, kept covers). Walking it
takes a moment, so the figure is kept per process for :data:`CACHE_SECONDS` and
raised by each file this process stores in the meantime; a file deleted by the
daily cleanup shows within that time.
"""

from __future__ import annotations

import time
import uuid

from app.core.config import get_settings
from app.db.models import Tenant
from app.pipeline.storage import Storage
from app.pipeline.uploads import UploadRejected

GB = 1024**3
CACHE_SECONDS = 300
_usage: dict[uuid.UUID, tuple[float, int]] = {}


class StorageFull(UploadRejected):
    status = 413


def default_bytes() -> int:
    return int(get_settings().storage_cap_gb * GB)


def limit(tenant: Tenant) -> int:
    """The account's cap in bytes: the admin's, else the default."""
    return tenant.storage_cap_bytes if tenant.storage_cap_bytes is not None else default_bytes()


def used(storage: Storage, tenant_id: uuid.UUID, *, fresh: bool = False) -> int:
    cached = _usage.get(tenant_id)
    if cached is not None and not fresh and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    files = getattr(storage, "files", None)
    total = sum(size for _, size, _ in files(str(tenant_id))) if files is not None else 0
    _usage[tenant_id] = (time.monotonic(), total)
    return total


def added(tenant_id: uuid.UUID, size: int) -> None:
    """This process stored ``size`` more bytes for the account."""
    cached = _usage.get(tenant_id)
    if cached is not None:
        _usage[tenant_id] = (cached[0], cached[1] + size)


def forget(tenant_id: uuid.UUID | None = None) -> None:
    if tenant_id is None:
        _usage.clear()
    else:
        _usage.pop(tenant_id, None)


def size(n: int) -> str:
    return f"{n / GB:.1f} GB" if n >= GB // 10 else f"{n / 1024**2:.0f} MB"


def message(in_use: int, cap: int, incoming: int, drafted_days: int, unpublished_days: int) -> str:
    return (
        f"Your stored images use {size(in_use)} of your {size(cap)} limit, so this image ({size(incoming)}) "
        f"cannot be added. Space frees up by itself: a listing's images are removed {drafted_days} days after it "
        f"has a draft in every shop it is meant for, and other uploads {unpublished_days} days after you last "
        "worked on them. To free space now, delete batches you no longer need on the Batches page."
    )
