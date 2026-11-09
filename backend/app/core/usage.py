"""The listing counter (fix/usage-and-admin-publishing, Part 1).

**The rule.** The allowance counts *listings*, whoever wrote them (our AI or the seller). A listing
(one design in a batch: its ``generated_content`` row) counts exactly once, when its **first** Etsy
draft is created successfully in any shop. Never counted: more shops (now or later), retries, failed or
half-made attempts, Replace images, crops, text edits, per-shop overrides, re-sending after a failure,
example listings, editing a listing already on Etsy. A new upload of the same design is a new listing.
The count belongs to the period in which that first draft was created (the account's allowance
period, in its time zone: today a calendar month).

**Guarantees.**

1. ``usage_event`` is unique per (account, listing) and is written by :func:`record_first_draft` in the
   same transaction as the draft's publication record (etsy/publisher.py): both commit or neither.
   The insert is ``ON CONFLICT DO NOTHING``, so a second shop's first draft at the same moment never
   fails or double counts.
2. :func:`used` is the one count; ``core/allowance.py::status`` reads it, and every screen, the
   allowance check, admin, ``limits-report`` and the health digest read ``status``.
3. :data:`BACKFILL` fills history from the publication records (the migration and ``usage-reconcile``
   use the same query).
4. :func:`reconcile` compares the two daily (``workers/usage.py``) and in ``usage-reconcile``.

**Shadow mode.** ``USAGE_COUNTER=shadow`` (the default): everything above is recorded, but screens and
the allowance check keep the old number (listings written); admin shows both. ``USAGE_COUNTER=on``
switches every reader to this count: a configuration change only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, GeneratedContent, ListingPublication, UploadBatch, UsageEvent

MODES = ("shadow", "on")

#: Each listing with a successful draft, its first draft's time, account, shop and record: the history
#: the counter starts from. A publication is written only when its draft finished (etsy/publisher.py);
#: one marked deleted on Etsy was still drafted, its listing kept in ``former_content_id``. One whose
#: listing is gone altogether (batch deleted) is its own key. Example listings never reach Etsy.
BACKFILL = """
SELECT DISTINCT ON (p.tenant_id, k.key)
       p.tenant_id, k.key, k.content_id, p.id, p.connection_id, p.created_at
FROM listing_publication p
CROSS JOIN LATERAL (
    SELECT COALESCE(CAST(COALESCE(p.content_id, p.former_content_id) AS TEXT), 'pub:' || CAST(p.id AS TEXT)) AS key,
           COALESCE(p.content_id, p.former_content_id) AS content_id
) k
ORDER BY p.tenant_id, k.key, p.created_at, p.id
"""


def mode() -> str:
    value = (get_settings().usage_counter or "shadow").strip().lower()
    return value if value in MODES else "shadow"


def key_of(content_id: uuid.UUID | None, publication_id: uuid.UUID) -> str:
    return str(content_id) if content_id is not None else f"pub:{publication_id}"


async def _insert(session: AsyncSession, values: dict) -> None:
    """Insert, doing nothing when the listing is counted already (Postgres and SQLite alike)."""
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    statement = insert(UsageEvent).values(**values).on_conflict_do_nothing(index_elements=["tenant_id", "listing_key"])
    await session.execute(statement)


async def record_first_draft(session: AsyncSession, publication: ListingPublication, content: GeneratedContent) -> None:
    """Count the listing if this is its first successful draft. Called before the draft's commit."""
    batch = await session.get(UploadBatch, content.batch_id)
    if batch is not None and batch.is_example:
        return  # never sent; said again here
    await _insert(session, {
        "id": uuid.uuid4(), "tenant_id": publication.tenant_id, "listing_key": key_of(content.id, publication.id),
        "content_id": content.id, "publication_id": publication.id, "connection_id": publication.connection_id,
        "at": datetime.now(timezone.utc), "source": "draft",
    })


async def used(session: AsyncSession, tenant_id: uuid.UUID, start: datetime, end: datetime) -> int:
    """Listings counted in [start, end): the one number every reader uses."""
    return int(await session.scalar(
        select(func.count()).select_from(UsageEvent)
        .where(UsageEvent.tenant_id == tenant_id, UsageEvent.at >= start, UsageEvent.at < end)
    ) or 0)


async def backfill(session: AsyncSession, tenant_id: uuid.UUID | None = None, *, source: str = "backfill") -> int:
    """Add the rows the publication records show and the counter lacks. Never deletes. Not committed."""
    rows = await _expected(session, tenant_id)
    have = await _counted(session, tenant_id)
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
    """BACKFILL, as rows; in Python on SQLite (no DISTINCT ON), the same answer."""
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
    return list(first.values())


async def _counted(session: AsyncSession, tenant_id: uuid.UUID | None) -> dict[tuple[uuid.UUID, str], UsageEvent]:
    query = select(UsageEvent)
    if tenant_id is not None:
        query = query.where(UsageEvent.tenant_id == tenant_id)
    return {(u.tenant_id, u.listing_key): u for u in (await session.execute(query)).scalars()}


@dataclass
class Reconciled:
    #: Listings with a successful draft and no usage row: (account, listing key).
    missing: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    #: Usage rows with no successful draft behind them, while their shop is still connected.
    orphans: list[tuple[uuid.UUID, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.missing and not self.orphans

    def lines(self) -> list[str]:
        out = [f"Listings with a draft but no usage row: {len(self.missing)}"]
        out += [f"  missing  account {t}  listing {k}" for t, k in self.missing]
        out.append(f"Usage rows with no successful draft: {len(self.orphans)}")
        out += [f"  orphan   account {t}  listing {k}" for t, k in self.orphans]
        return out


async def reconcile(session: AsyncSession, tenant_id: uuid.UUID | None = None) -> Reconciled:
    """Compare the counter with the publication records. Read only."""
    expected = {(row[0], row[1]) for row in await _expected(session, tenant_id)}
    counted = await _counted(session, tenant_id)
    connected = set((await session.execute(
        select(EtsyConnection.id).where(EtsyConnection.status == ConnectionStatus.active))).scalars())
    out = Reconciled()
    out.missing = sorted((t, k) for t, k in expected if (t, k) not in counted)
    for (t, k), row in sorted(counted.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        # A disconnected shop's drafts were purged with it: its counts are history, not orphans.
        if (t, k) not in expected and (row.connection_id is None or row.connection_id in connected):
            out.orphans.append((t, k))
    return out
