"""What the stored image files are, per account and kind, and whether they are still needed.

Read by ``python -m app.cli storage-report``. Sizes, counts and ages only: an
account is shown by the first 8 characters of its id; no file name, design,
listing text or personal data is printed.

Kinds, from where a file sits (``{tenant}/{batch}/{folder}/{asset}...``):
  * ``original``: the upload as the seller sent it (``original/``)
  * ``processed``: the Etsy-ready copy every later step reads (``processed/``)
  * ``kept cover``: the small cover JPEG kept after retention (``kept/``); cover
    crops themselves are not stored, they are cut from the processed copy when
    a draft is made
  * ``preview``: resized previews cached beside any of the above (``.v<N>.w<W>``)

Each file is counted under its listing group's state (pipeline/group_state.py):
drafted in every target shop / pending (scheduled, distributed, work queued) /
in review / files removed (kept covers) / no row (a file no asset points at).
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asset
from app.pipeline import group_state as gs
from app.pipeline.storage import LocalStorage

KINDS = ("original", "processed", "kept cover", "preview")
STATES = (gs.DRAFTED, gs.PENDING, gs.IN_REVIEW, gs.REMOVED, "no row")
STATE_LABELS = {
    gs.DRAFTED: "drafted in every target shop", gs.PENDING: "scheduled / pending",
    gs.IN_REVIEW: "still in review", gs.REMOVED: "files removed", "no row": "no row",
}
#: Age buckets, by the file's modification time: (upper bound in days, label).
AGES = ((1, "<1d"), (3, "1-3d"), (7, "3-7d"), (14, "7-14d"), (30, "14-30d"), (None, "30d+"))
_PREVIEW = re.compile(r"\.v\d+\.w\d+")
_FOLDERS = {"original": "original", "processed": "processed", "kept": "kept cover"}


def kind_of(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) != 4:
        return None
    if _PREVIEW.search(parts[3]):
        return "preview"
    return _FOLDERS.get(parts[2])


def _asset_id(key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(key.split("/")[3].split(".")[0])
    except (IndexError, ValueError):
        return None


def age_label(days: float) -> str:
    for bound, label in AGES:
        if bound is None or days < bound:
            return label
    return AGES[-1][1]


@dataclass
class Bucket:
    files: int = 0
    bytes: int = 0
    ages: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # bytes per age bucket
    states: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # bytes per group state

    def add(self, size: int, age: str, state: str) -> None:
        self.files += 1
        self.bytes += size
        self.ages[age] += size
        self.states[state] += size

    @property
    def average(self) -> int:
        return self.bytes // self.files if self.files else 0


@dataclass
class Report:
    #: account id prefix -> kind -> bucket
    accounts: dict[str, dict[str, Bucket]]
    total: dict[str, Bucket]
    other_bytes: int = 0  # files outside the {tenant}/{batch}/{folder}/ layout

    def account_bytes(self, prefix: str) -> int:
        return sum(b.bytes for b in self.accounts[prefix].values())


async def build(session: AsyncSession, storage: LocalStorage, *, now: float | None = None) -> Report:
    now = now or time.time()
    states = await gs.group_states(session, with_removed=True)
    state_of: dict[uuid.UUID, str] = {}
    for g in states.values():
        for a in g.assets:
            state_of[a.id] = g.state
    # Assets whose group is not in ``states`` (none should be): count as their own row.
    known = {a for (a,) in (await session.execute(select(Asset.id))).all()}
    accounts: dict[str, dict[str, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    total: dict[str, Bucket] = defaultdict(Bucket)
    other = 0
    for key, size, mtime in storage.files():
        kind = kind_of(key)
        if kind is None:
            other += size
            continue
        asset = _asset_id(key)
        if kind == "kept cover":
            state = gs.REMOVED
        elif asset in state_of:
            state = state_of[asset]
        else:
            state = gs.REMOVED if asset in known else "no row"
        age = age_label(max(0.0, now - mtime) / 86400)
        account = key.split("/")[0][:8]
        accounts[account][kind].add(size, age, state)
        total[kind].add(size, age, state)
    return Report(accounts=dict(accounts), total=dict(total), other_bytes=other)


def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB" if n >= 1024**3 // 100 else f"{n / 1024**2:.1f} MB"


def render(report: Report) -> str:
    lines: list[str] = []

    def block(title: str, kinds: dict[str, Bucket]) -> None:
        size = sum(b.bytes for b in kinds.values())
        lines.append(f"{title}: {_gb(size)} in {sum(b.files for b in kinds.values()):,} files")
        for kind in KINDS:
            b = kinds.get(kind)
            if b is None or not b.files:
                continue
            ages = "  ".join(f"{label} {_gb(b.ages.get(label, 0))}" for _, label in AGES if b.ages.get(label))
            states = "  ".join(f"{STATE_LABELS[s]} {_gb(b.states[s])}" for s in STATES if b.states.get(s))
            lines.append(f"  {kind:<11} {b.files:>7,} files  {_gb(b.bytes):>10}  avg {_gb(b.average):>9}")
            lines.append(f"      age:   {ages}")
            lines.append(f"      state: {states}")

    block("All accounts", report.total)
    for prefix in sorted(report.accounts, key=report.account_bytes, reverse=True):
        lines.append("")
        block(f"Account {prefix}…", report.accounts[prefix])
    if report.other_bytes:
        lines.append(f"\nOther files in storage: {_gb(report.other_bytes)}")
    return "\n".join(lines)


def as_dict(report: Report) -> dict[str, Any]:
    def bucket(b: Bucket) -> dict[str, Any]:
        return {"files": b.files, "bytes": b.bytes, "average": b.average, "ages": dict(b.ages), "states": dict(b.states)}

    return {
        "total": {k: bucket(b) for k, b in report.total.items()},
        "accounts": {p: {k: bucket(b) for k, b in kinds.items()} for p, kinds in report.accounts.items()},
        "other_bytes": report.other_bytes,
    }
