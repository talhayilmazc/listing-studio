""""Delete my sales history" for one shop (the profit report; Commercial Access review).

The seller's own sales records are kept for the Analytics history setting (24 months by default)
to build the profit report. The seller can delete them for a shop at any time: the order lines,
the daily sales and fee totals, and the figures from the statements and Etsy Ads reports they
imported. Nothing on Etsy changes.

Later reads fetch only what the seller still wants shown: the shop's ``sales_history_from`` is
set to the day of the deletion, and every place that writes sales or fee totals drops anything
older (workers/sales.py ``_add_totals``, workers/ledger.py ``_add``), whatever read brings it in
(the nightly update and its 30-day re-read, a first read, the ledger's backward history or the
one-time re-read). A read of the history under way stops; the nightly update carries on from
the day of the deletion. Disconnecting the shop deletes everything anyway and clears the date
(workers/retention.py), so a reconnected shop starts fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdCharge,
    AdsDaily,
    EtsyConnection,
    LedgerDaily,
    LedgerSync,
    SaleLine,
    SalesDaily,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
)

#: The tables "Delete my sales history" empties for the shop, by what the seller calls them.
TABLES: tuple[tuple[str, type], ...] = (
    ("sale_lines", SaleLine),
    ("sales_days", SalesDaily),
    ("fee_days", LedgerDaily),
    ("statement_months", StatementImport),
    ("statement_orders", StatementOrder),
    ("statement_listing_fees", StatementListingFee),
    ("ad_charges", AdCharge),
    ("ads_days", AdsDaily),
)

#: What the confirmation says, word for word (the button, the dialog and the policy agree).
WHAT_IS_DELETED = (
    "This shop's order lines, its daily sales and fee totals, and the figures from statements and Etsy Ads reports "
    "you imported. Nothing on Etsy changes."
)


@dataclass
class Deleted:
    counts: dict[str, int]
    history_from: date


def _midnight(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def floor_of(connection: EtsyConnection) -> date | None:
    """The first day the seller still wants shown (None: everything the setting keeps)."""
    return connection.sales_history_from


async def delete_history(session: AsyncSession, connection: EtsyConnection, *, today: date | None = None) -> Deleted:
    """Delete the shop's stored sales history and keep later reads from bringing it back.
    Does not commit."""
    day = today or datetime.now(timezone.utc).date()
    counts: dict[str, int] = {}
    for name, table in TABLES:
        result = await session.execute(delete(table).where(table.connection_id == connection.id))
        counts[name] = int(result.rowcount or 0)
    connection.sales_history_from = day
    now = datetime.now(timezone.utc)

    sales = await session.get(SalesSync, connection.id)
    if sales is not None:
        # The window starts at the deletion: the report says so instead of showing zeros before it.
        sales.window_start = max(sales.window_start or day, day)
        sales.read_count = 0
        sales.oldest_ts = sales.oldest_id = None
        if sales.state in ("reading", "waiting"):
            # A read of the history stops; from now on only the nightly update runs.
            sales.state, sales.finished_at, sales.resumes_at, sales.note = "complete", now, None, None
        if sales.reread == "reading" or not sales.has_lines:
            sales.reread, sales.has_lines = "done", True  # nothing to read again before the deletion

    ledger = await session.get(LedgerSync, connection.id)
    if ledger is not None:
        edge = _midnight(day)
        if ledger.state in ("reading", "waiting"):
            ledger.state, ledger.finished_at, ledger.resumes_at, ledger.note = "complete", now, None, None
            ledger.synced_until, ledger.update_end, ledger.next_offset = int(now.timestamp()), None, 0
        if ledger.window_start is not None:
            ledger.window_start = max(ledger.window_start, edge)
        # The backward history ends at the deletion: it has nothing left to fill in.
        ledger.covered_from, ledger.backfill_target = edge, edge
        ledger.slice_start, ledger.slice_offset = None, 0
        if ledger.state == "complete":  # an update cut short: the next one starts afresh (its pages are gone)
            ledger.update_end, ledger.next_offset = None, 0
        if ledger.backfill_state != "none" or ledger.state == "complete":
            ledger.backfill_state, ledger.backfill_note = "complete", None
        ledger.read_count = 0
    return Deleted(counts=counts, history_from=day)
