"""The listing counter (fix/usage-and-admin-publishing, Part 1).

**The rule.** The allowance counts *listings*, whoever wrote them (our AI or the seller). A listing
(one design in a batch: its ``generated_content`` row) counts exactly once, at the **earlier** of
(a) its first successful AI write, or (b) its first successful Etsy draft in any shop. An AI-written
listing that is never sent still counts: our AI wrote it, and its text could be copied out by hand. A
hand-written listing counts at its first draft. Never counted again: rewriting the text of a listing
already counted (a regenerate moves its row to the new text: :func:`record_ai_write`), more shops (now
or later), retries, failed or half-made attempts, Replace images, crops, text edits, per-shop
overrides, re-sending after a failure, editing a listing already on Etsy. Example listings never count.
A new upload of the same design is a new listing. The count belongs to the period of the earlier event
(the account's allowance period, in its time zone).

**Guarantees.**

1. ``usage_event`` is unique per (account, listing). :func:`record_ai_write` writes it in the same
   transaction as the AI result (pipeline/generation.py, api/another_shop.py), :func:`record_first_draft`
   in the same transaction as the draft's publication record (etsy/publisher.py): whichever comes
   second finds the row and adds nothing (``ON CONFLICT``; the draft only links itself to a row the AI
   write made). An AI write and a first draft at the same moment never fail or double count.
2. :func:`used` is the one count; ``core/allowance.py::status`` reads it, and every screen, the
   allowance check, admin, ``limits-report`` and the health digest read ``status``.
3. :data:`BACKFILL` fills history from the publication records and the AI-written listings (migration
   0100 and ``usage-reconcile`` use the same query); an AI write's time is its listing's ``created_at``.
4. :func:`reconcile` compares the counter with both, daily (``workers/usage.py``) and in
   ``usage-reconcile``.

**Shadow mode.** ``USAGE_COUNTER=shadow`` (the default): everything above is recorded, but screens and
the allowance check keep the old number (listings written); admin shows both. ``USAGE_COUNTER=on``
switches every reader to this count: a configuration change only.

**Never a gate on scheduled work.** No draft, go-live or schedule path reads the allowance: a listing
scheduled before the limit was reached is drafted and goes live whatever the count says, in both modes
(tests/test_usage.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, GeneratedContent, ListingPublication, UploadBatch, UsageEvent

MODES = ("shadow", "on")

#: Each listing the counter should hold, at the earlier of its first AI write and its first draft: the
#: history the counter starts from. Drafts: a publication is written only when its draft finished
#: (etsy/publisher.py); one marked deleted on Etsy was still drafted, its listing kept in
#: ``former_content_id``; one whose listing is gone altogether (batch deleted) is its own key. AI writes:
#: every listing our AI wrote (``source = 'ai'``), at its ``created_at``; a hand-written listing counts
#: only through its drafts. Example listings never reach Etsy and are never counted.
BACKFILL = """
WITH d AS (
    SELECT DISTINCT ON (p.tenant_id, k.key)
           p.tenant_id, k.key, k.content_id, p.id, p.connection_id, p.created_at
    FROM listing_publication p
    CROSS JOIN LATERAL (
        SELECT COALESCE(CAST(COALESCE(p.content_id, p.former_content_id) AS TEXT), 'pub:' || CAST(p.id AS TEXT)) AS key,
               COALESCE(p.content_id, p.former_content_id) AS content_id
    ) k
    ORDER BY p.tenant_id, k.key, p.created_at, p.id
), w AS (
    SELECT c.tenant_id, CAST(c.id AS TEXT) AS key, c.id AS content_id, c.created_at
    FROM generated_content c
    JOIN upload_batch b ON b.id = c.batch_id
    WHERE c.source = 'ai' AND NOT b.is_example
)
SELECT COALESCE(d.tenant_id, w.tenant_id) AS tenant_id, COALESCE(d.key, w.key) AS key,
       COALESCE(d.content_id, w.content_id) AS content_id, d.id AS publication_id, d.connection_id,
       LEAST(d.created_at, w.created_at) AS at
FROM d FULL OUTER JOIN w ON w.tenant_id = d.tenant_id AND w.key = d.key
"""


def mode() -> str:
    value = (get_settings().usage_counter or "shadow").strip().lower()
    return value if value in MODES else "shadow"


def key_of(content_id: uuid.UUID | None, publication_id: uuid.UUID) -> str:
    return str(content_id) if content_id is not None else f"pub:{publication_id}"


def _insert_statement(session: AsyncSession, values: dict) -> Any:
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert(UsageEvent).values(**values)


async def _insert(session: AsyncSession, values: dict) -> None:
    """Insert, doing nothing when the listing is counted already (Postgres and SQLite alike)."""
    statement = _insert_statement(session, values).on_conflict_do_nothing(index_elements=["tenant_id", "listing_key"])
    await session.execute(statement)


async def _is_example(session: AsyncSession, content: GeneratedContent) -> bool:
    batch = await session.get(UploadBatch, content.batch_id)
    return batch is None or batch.is_example


async def record_ai_write(session: AsyncSession, tenant_id: uuid.UUID, content: GeneratedContent,
                          *, replaces: Iterable[uuid.UUID] = ()) -> None:
    """Count the listing at a successful AI write unless it is counted already. Called before the
    write's commit. ``replaces``: the listing rows a regenerate retires; when one of them is counted,
    its row moves to the new text (same time, same period) instead of a second count."""
    if await _is_example(session, content):
        return  # written once by us for the example batch: never counted
    key = str(content.id)
    old_keys = [str(r) for r in replaces if r != content.id]
    if old_keys:
        counted = (await session.execute(
            select(UsageEvent).where(UsageEvent.tenant_id == tenant_id, UsageEvent.listing_key.in_(old_keys))
            .order_by(UsageEvent.at).limit(1)
        )).scalars().first()
        if counted is not None:
            mine = await session.scalar(select(UsageEvent.id).where(UsageEvent.tenant_id == tenant_id,
                                                                    UsageEvent.listing_key == key))
            if mine is None:
                counted.listing_key, counted.content_id = key, content.id
                await session.flush()
            return
    await _insert(session, {
        "id": uuid.uuid4(), "tenant_id": tenant_id, "listing_key": key, "content_id": content.id,
        "publication_id": None, "connection_id": None, "at": datetime.now(timezone.utc), "source": "ai_write",
    })


async def record_first_draft(session: AsyncSession, publication: ListingPublication, content: GeneratedContent) -> None:
    """Count the listing if this is its first successful draft and it is not counted yet. Called before
    the draft's commit. A listing already counted at its AI write only gets this draft linked as its
    first (ids, so reconciliation still matches once its batch is gone); its count and time stay."""
    if await _is_example(session, content):
        return  # never sent; said again here
    statement = _insert_statement(session, {
        "id": uuid.uuid4(), "tenant_id": publication.tenant_id, "listing_key": key_of(content.id, publication.id),
        "content_id": content.id, "publication_id": publication.id, "connection_id": publication.connection_id,
        "at": datetime.now(timezone.utc), "source": "draft",
    })
    statement = statement.on_conflict_do_update(
        index_elements=["tenant_id", "listing_key"],
        set_={"publication_id": statement.excluded.publication_id, "connection_id": statement.excluded.connection_id},
        where=UsageEvent.__table__.c.publication_id.is_(None),
    )
    await session.execute(statement)


async def used(session: AsyncSession, tenant_id: uuid.UUID, start: datetime, end: datetime) -> int:
    """Listings counted in [start, end): the one number every reader uses."""
    return int(await session.scalar(
        select(func.count()).select_from(UsageEvent)
        .where(UsageEvent.tenant_id == tenant_id, UsageEvent.at >= start, UsageEvent.at < end)
    ) or 0)


async def backfill(session: AsyncSession, tenant_id: uuid.UUID | None = None, *, source: str = "backfill") -> int:
    """Add the rows the publication records and AI-written listings show and the counter lacks. Never
    deletes. Not committed."""
    rows = await _expected(session, tenant_id)
    have = _covered(await _counted(session, tenant_id), await _publication_keys(session, tenant_id))
    added = 0
    for tenant, key, content_id, publication_id, connection_id, at in rows:
        if (tenant, key) in have:
            continue
        await _insert(session, {"id": uuid.uuid4(), "tenant_id": tenant, "listing_key": key, "content_id": content_id,
                                "publication_id": publication_id, "connection_id": connection_id, "at": at,
                                "source": source})
        added += 1
    return added


async def _expected(session: AsyncSession, tenant_id: uuid.UUID | None) -> list[tuple]:
    """BACKFILL, as rows; in Python on SQLite (no DISTINCT ON, no FULL JOIN), the same answer."""
    query = select(ListingPublication.tenant_id, ListingPublication.content_id, ListingPublication.former_content_id,
                   ListingPublication.id, ListingPublication.connection_id, ListingPublication.created_at)
    if tenant_id is not None:
        query = query.where(ListingPublication.tenant_id == tenant_id)
    first: dict[tuple[uuid.UUID, str], tuple] = {}
    for tenant, content_id, former, pub_id, connection_id, created in (
        await session.execute(query.order_by(ListingPublication.created_at, ListingPublication.id))
    ).all():
        listing = content_id or former
        key = (tenant, key_of(listing, pub_id))
        if key not in first:
            first[key] = (tenant, key[1], listing, pub_id, connection_id, created)
    written = (select(GeneratedContent.tenant_id, GeneratedContent.id, GeneratedContent.created_at)
               .join(UploadBatch, UploadBatch.id == GeneratedContent.batch_id)
               .where(GeneratedContent.source == "ai", UploadBatch.is_example.is_(False)))
    if tenant_id is not None:
        written = written.where(GeneratedContent.tenant_id == tenant_id)
    for tenant, content_id, created in (await session.execute(written)).all():
        key = (tenant, str(content_id))
        drafted = first.get(key)
        if drafted is None:
            first[key] = (tenant, key[1], content_id, None, None, created)
        elif _aware(created) < _aware(drafted[5]):
            first[key] = (*drafted[:5], created)
    return list(first.values())


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


async def _counted(session: AsyncSession, tenant_id: uuid.UUID | None) -> dict[tuple[uuid.UUID, str], UsageEvent]:
    query = select(UsageEvent)
    if tenant_id is not None:
        query = query.where(UsageEvent.tenant_id == tenant_id)
    return {(u.tenant_id, u.listing_key): u for u in (await session.execute(query)).scalars()}


async def _publication_keys(session: AsyncSession, tenant_id: uuid.UUID | None) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """Each draft's listing key, by the draft's id."""
    query = select(ListingPublication.id, ListingPublication.tenant_id, ListingPublication.content_id,
                   ListingPublication.former_content_id)
    if tenant_id is not None:
        query = query.where(ListingPublication.tenant_id == tenant_id)
    return {pid: (t, key_of(c or f, pid)) for pid, t, c, f in (await session.execute(query)).all()}


def _covered(counted: dict[tuple[uuid.UUID, str], UsageEvent], pub_key: dict) -> set[tuple[uuid.UUID, str]]:
    """The listings the counter holds: each row's own key, and its first draft's key (a row made at the AI
    write keeps covering its listing once the batch is gone and the draft has become a ``pub:`` key)."""
    covered = set(counted)
    covered |= {pub_key[row.publication_id] for row in counted.values() if row.publication_id in pub_key}
    return covered


@dataclass
class Reconciled:
    #: Listings written by our AI or with a successful draft, and no usage row: (account, listing key).
    missing: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    #: Usage rows with no AI write and no successful draft behind them, while that could still be seen.
    orphans: list[tuple[uuid.UUID, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.missing and not self.orphans

    def lines(self) -> list[str]:
        out = [f"Listings written by our AI or drafted, with no usage row: {len(self.missing)}"]
        out += [f"  missing  account {t}  listing {k}" for t, k in self.missing]
        out.append(f"Usage rows with no AI write and no successful draft: {len(self.orphans)}")
        out += [f"  orphan   account {t}  listing {k}" for t, k in self.orphans]
        return out


async def reconcile(session: AsyncSession, tenant_id: uuid.UUID | None = None) -> Reconciled:
    """Compare the counter with the publication records and the AI-written listings. Read only."""
    expected = {(row[0], row[1]) for row in await _expected(session, tenant_id)}
    counted = await _counted(session, tenant_id)
    pub_key = await _publication_keys(session, tenant_id)
    covered = _covered(counted, pub_key)
    ids = [row.content_id for row in counted.values() if row.content_id is not None]
    contents: dict[uuid.UUID, bool] = dict((await session.execute(
        select(GeneratedContent.id, UploadBatch.is_example)
        .join(UploadBatch, UploadBatch.id == GeneratedContent.batch_id)
        .where(GeneratedContent.id.in_(ids))
    )).all()) if ids else {}
    connected = set((await session.execute(
        select(EtsyConnection.id).where(EtsyConnection.status == ConnectionStatus.active))).scalars())
    out = Reconciled()
    out.missing = sorted((t, k) for t, k in expected if (t, k) not in covered)
    for (t, k), row in sorted(counted.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        if (t, k) in expected or row.publication_id in pub_key:
            continue
        # A disconnected shop's drafts were purged with it, a deleted batch's listings with it: their
        # counts are history, not orphans.
        if row.connection_id is not None and row.connection_id not in connected:
            continue
        if row.content_id is not None and row.content_id not in contents:
            continue
        # Our AI wrote a shop's text for a listing the seller wrote (Rewrite for this shop): the listing
        # itself is the evidence, unless it is an example.
        if row.source == "ai_write" and contents.get(row.content_id) is False:
            continue
        out.orphans.append((t, k))
    return out
