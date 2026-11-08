"""The sign-up funnel, first-party only: one row per account and step, written once (``funnel_event``).

What a new seller has done so far, in order: signed up, connected a shop, uploaded a first design,
had a first listing ready, approved a first listing, sent a first one to Etsy (as drafts or on a
schedule). Each is written the first time it happens and never again (unique on account + name),
so the time a step took is the difference of two rows. Nothing but the account id, the step's name
and its time is stored: no e-mail, shop, title, file name, address or request detail.

The example designs (pipeline/examples.py) write nothing here: trying them is not a step.

Adding a step later: add its name to ``NAMES`` (in the order a seller meets it; the admin page and
the conversions follow this order), write it where it happens with ``record``, and say in
``BACKFILL`` how to find it in data that existed before (or nothing, if it cannot be known).
The first-run checklist (pipeline/onboarding.py) reads the same rows.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FunnelEvent, Tenant, UploadBatch

SIGNED_UP = "signed_up"
SHOP_CONNECTED = "shop_connected"
FIRST_UPLOAD = "first_upload"
FIRST_LISTING_READY = "first_listing_ready"
FIRST_APPROVED = "first_approved"
FIRST_SENT = "first_sent"

#: Every step, in the order a seller meets them.
NAMES: tuple[str, ...] = (SIGNED_UP, SHOP_CONNECTED, FIRST_UPLOAD, FIRST_LISTING_READY, FIRST_APPROVED, FIRST_SENT)

LABELS: dict[str, str] = {
    SIGNED_UP: "Signed up",
    SHOP_CONNECTED: "Connected a shop",
    FIRST_UPLOAD: "Uploaded a first design",
    FIRST_LISTING_READY: "First listing ready",
    FIRST_APPROVED: "Approved a first listing",
    FIRST_SENT: "Sent a first listing",
}


async def record(session: AsyncSession, tenant_id: uuid.UUID, name: str, *, at: datetime | None = None) -> None:
    """Write ``name`` for the account unless it is already there. Part of the caller's transaction;
    a second time (or a second request at the same moment) changes nothing."""
    if name not in NAMES:
        raise ValueError(f"unknown funnel step {name!r}: add it to funnel.NAMES")
    values = {"id": uuid.uuid4(), "tenant_id": tenant_id, "name": name, "at": at or datetime.now(timezone.utc)}
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    await session.execute(
        insert(FunnelEvent).values(**values).on_conflict_do_nothing(index_elements=["tenant_id", "name"])
    )


async def record_for_batch(session: AsyncSession, tenant_id: uuid.UUID, name: str, batch_id: uuid.UUID | None) -> None:
    """``record``, unless the batch is the example designs' (they are not a step)."""
    if batch_id is not None:
        is_example = await session.scalar(select(UploadBatch.is_example).where(UploadBatch.id == batch_id))
        if is_example:
            return
    await record(session, tenant_id, name)


async def done(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, datetime]:
    """The steps the account has done, with when."""
    rows = await session.execute(select(FunnelEvent.name, FunnelEvent.at).where(FunnelEvent.tenant_id == tenant_id))
    return {name: at for name, at in rows}


# --- What existed before (migration 0098 and ``python -m app.cli funnel-backfill``) ----------------
#: Per step, a query giving (tenant_id, first time) from the app's own data; nothing else is read.
#: Example batches are left out; the time is the first such row's own time. A step a query cannot
#: see is simply not written: a seller who sent before approval times were kept (migration 0091)
#: gets ``first_approved`` from nothing, and the admin page counts them as not known.
BACKFILL: dict[str, str] = {
    SIGNED_UP: "SELECT id, created_at FROM tenant",
    SHOP_CONNECTED: "SELECT tenant_id, min(connected_at) FROM etsy_connection GROUP BY tenant_id",
    # An asset keeps no time of its own: its batch's, for a batch with at least one photo.
    FIRST_UPLOAD: (
        "SELECT b.tenant_id, min(b.created_at) FROM upload_batch b "
        "WHERE NOT b.is_example AND EXISTS (SELECT 1 FROM asset a WHERE a.batch_id = b.id) GROUP BY b.tenant_id"
    ),
    FIRST_LISTING_READY: (
        "SELECT c.tenant_id, min(c.created_at) FROM generated_content c JOIN upload_batch b ON b.id = c.batch_id "
        "WHERE NOT b.is_example GROUP BY c.tenant_id"
    ),
    FIRST_APPROVED: (
        "SELECT c.tenant_id, min(c.approved_at) FROM generated_content c JOIN upload_batch b ON b.id = c.batch_id "
        "WHERE NOT b.is_example AND c.approved_at IS NOT NULL GROUP BY c.tenant_id"
    ),
    FIRST_SENT: (
        "SELECT tenant_id, min(at) FROM ("
        " SELECT tenant_id, created_at AS at FROM listing_publication"
        " UNION ALL SELECT tenant_id, created_at AS at FROM group_plan"
        ") sent GROUP BY tenant_id"
    ),
}


async def backfill(session: AsyncSession) -> dict[str, int]:
    """Write every step that existing data shows and that is not written yet; how many per step.
    Safe to run again: nothing already written changes."""
    written: dict[str, int] = {}
    for name in NAMES:
        query = BACKFILL.get(name)
        if not query:
            continue
        rows = (await session.execute(text(query))).all()
        before = await session.scalar(select(func.count()).select_from(FunnelEvent).where(FunnelEvent.name == name))
        for tenant_id, at in rows:
            if at is None:
                continue
            tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
            when = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            await record(session, tid, name, at=when)
        after = await session.scalar(select(func.count()).select_from(FunnelEvent).where(FunnelEvent.name == name))
        written[name] = int(after or 0) - int(before or 0)
    await session.commit()
    return written


# --- The admin page ---------------------------------------------------------------------------------
@dataclass
class Step:
    name: str
    label: str
    count: int
    #: Of those who did the step before, the share who did this one (None for the first step).
    from_previous: float | None


@dataclass
class Funnel:
    days: int
    #: Accounts that signed up in the window; every later count is of these accounts only.
    cohort: int
    steps: list[Step]
    #: Median time from signing up to the first approval, for those who approved; None: nobody yet.
    median_to_first_approval: timedelta | None
    approved_with_time: int


async def report(session: AsyncSession, days: int, *, now: datetime | None = None) -> Funnel:
    """The accounts that signed up in the last ``days`` days and how far each got.

    A cohort, not a sum of events: "Approved a first listing" counts the accounts of this window
    that have approved one by now, whenever they did it. Demo accounts (``@example.test``) are left
    out. Each step's conversion is from the step before it."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    signed = (await session.execute(
        select(FunnelEvent.tenant_id, FunnelEvent.at)
        .join(Tenant, Tenant.id == FunnelEvent.tenant_id)
        .where(FunnelEvent.name == SIGNED_UP, FunnelEvent.at >= since, ~Tenant.email.like("%@example.test"))
    )).all()
    cohort = {tid: at for tid, at in signed}
    reached: dict[str, dict[uuid.UUID, datetime]] = {SIGNED_UP: dict(cohort)}
    if cohort:
        rows = await session.execute(
            select(FunnelEvent.tenant_id, FunnelEvent.name, FunnelEvent.at)
            .where(FunnelEvent.tenant_id.in_(list(cohort)), FunnelEvent.name != SIGNED_UP)
        )
        for tid, name, at in rows:
            reached.setdefault(name, {})[tid] = at
    steps: list[Step] = []
    previous: int | None = None
    for name in NAMES:
        count = len(reached.get(name, {}))
        share = None if previous is None else (count / previous if previous else 0.0)
        steps.append(Step(name=name, label=LABELS[name], count=count, from_previous=share))
        previous = count
    waits = [
        _aware(at) - _aware(cohort[tid])
        for tid, at in reached.get(FIRST_APPROVED, {}).items()
        if tid in cohort and _aware(at) >= _aware(cohort[tid])
    ]
    median = timedelta(seconds=statistics.median(w.total_seconds() for w in waits)) if waits else None
    return Funnel(days=days, cohort=len(cohort), steps=steps, median_to_first_approval=median, approved_with_time=len(waits))


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
