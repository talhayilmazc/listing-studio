"""Deleting every piece of stored Etsy data, for all accounts at once.

Two commands share this (``python -m app.cli``):

* ``purge-etsy-data``: the last step of the "Etsy access ended" runbook
  (docs/etsy-access-ended.md). If Etsy ends our API access we must stop using
  its data immediately: after ``ETSY_CALLS_BLOCKED`` is on and sellers are told,
  this deletes everything we hold that came from Etsy.
* ``scrub-etsy-data``: the same wipe on a **non-production** database, plus
  making shop names anonymous, for a dev machine that was given real data
  before development moved to synthetic data only. It refuses to run when
  ``APP_ENV=production``.

Both only count unless ``--apply`` is given.

What goes: every shop's tokens (so no call can be made), its queued jobs,
scheduled go-lives and planned drafts, the listing cache, the saved copies of
listings, the sales totals and order lines, the ledger totals, what "Import from
Etsy" stored, the Ads report totals, listing views and favourites, the text
versions sent to Etsy, the records of drafts and live listings (they hold Etsy
listing ids), the profiles (they are built from Etsy reference listings) and
their links to shops, and each shop's Etsy ids, granted scopes, section list and
listing counts. A shop's row stays, disconnected and empty, so the account's own
batches still point somewhere.

What stays: the accounts, the seller's own uploads and the listing text written
in the app (theirs, not Etsy's), audit rows (ids and counts) and our own cost
records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.db.models import (
    AdCharge,
    AdsDaily,
    ConnectionStatus,
    ContentVersion,
    DraftAttempt,
    DraftDismissal,
    EtsyConnection,
    Job,
    JobStatus,
    LedgerDaily,
    LedgerSync,
    ListingProfile,
    ListingPublication,
    ListingSnapshot,
    ListingStatDaily,
    PlannedSlot,
    ProfileShopLink,
    SaleLine,
    SalesDaily,
    SalesSync,
    ShopListingCache,
    StatementImport,
    StatementListingFee,
    StatementOrder,
)

#: Why queued work did not run.
CANCELLED = "cancelled: Etsy data was removed"

#: Every table whose rows are Etsy data or derived from it, in an order that
#: respects their foreign keys. ``tests/test_etsy_data_wipe.py`` fails if a table
#: with a ``connection_id`` is added to the models and not listed here (or
#: declared as kept).
ETSY_TABLES: tuple[type, ...] = (
    ShopListingCache,
    ListingSnapshot,
    SaleLine,
    SalesDaily,
    SalesSync,
    LedgerDaily,
    LedgerSync,
    StatementOrder,
    StatementListingFee,
    StatementImport,
    AdCharge,
    AdsDaily,
    ListingStatDaily,
    ContentVersion,
    DraftAttempt,
    DraftDismissal,
    ListingPublication,
    ProfileShopLink,
    ListingProfile,
)


class Refused(Exception):
    """The command may not run here."""


def refuse_in_production() -> None:
    env = get_settings().app_env.strip().lower()
    if env == "production":
        raise Refused(
            "scrub-etsy-data does not run when APP_ENV=production. On the production server, "
            "the only wipe is purge-etsy-data, the last step of docs/etsy-access-ended.md."
        )


async def counts(session: AsyncSession) -> dict[str, int]:
    """What a wipe would remove, by table (rows) and for the shops (tokens, work)."""
    out: dict[str, int] = {}
    for table in ETSY_TABLES:
        out[table.__tablename__] = int(await session.scalar(select(func.count()).select_from(table)) or 0)
    out["shops_with_tokens"] = int(await session.scalar(
        select(func.count()).select_from(EtsyConnection).where(EtsyConnection.access_token_enc.is_not(None))
    ) or 0)
    out["shops"] = int(await session.scalar(select(func.count()).select_from(EtsyConnection)) or 0)
    out["queued_jobs"] = int(await session.scalar(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.queued)
    ) or 0)
    out["planned_drafts"] = int(await session.scalar(
        select(func.count()).select_from(PlannedSlot).where(PlannedSlot.state.in_(("waiting", "drafting")))
    ) or 0)
    return out


async def wipe(session: AsyncSession, *, apply: bool, anonymise_shops: bool = False, action: str = "etsy_data.purged") -> dict[str, Any]:
    """Count, and with ``apply`` delete, all stored Etsy data. Commits when applying."""
    before = await counts(session)
    if not apply:
        return {"applied": False, **before}
    now = datetime.now(timezone.utc)
    # Tokens first: from this statement on no call can be made for any shop.
    await session.execute(
        update(EtsyConnection).values(
            access_token_enc=None, refresh_token_enc=None, token_expires_at=None, status=ConnectionStatus.revoked,
            scopes=None, sections=None, sections_at=None, listing_counts=None, listing_counts_at=None,
            sales_synced_at=None,
        ).execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Job).where(Job.status == JobStatus.queued)
        .values(status=JobStatus.cancelled, last_error=CANCELLED, paused_reason=None, finished_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(PlannedSlot).where(PlannedSlot.state.in_(("waiting", "drafting")))
        .values(state="cancelled", note=CANCELLED).execution_options(synchronize_session=False)
    )
    for table in ETSY_TABLES:
        await session.execute(delete(table).execution_options(synchronize_session=False))
    if anonymise_shops:
        # A dev machine keeps the shop rows (batches point at them) under made-up
        # names, with no Etsy ids left to call or to recognise a shop by.
        shops = list((await session.execute(select(EtsyConnection).order_by(EtsyConnection.connected_at))).scalars())
        for n, shop in enumerate(shops, start=1):
            shop.shop_name, shop.display_name = f"Demo shop {n}", None
            shop.etsy_user_id, shop.shop_id = None, None
    else:
        await session.execute(
            update(EtsyConnection).values(shop_name=None, etsy_user_id=None, shop_id=None)
            .execution_options(synchronize_session=False)
        )
    audit.record(session, action, actor=None, by="system", **{k: v for k, v in before.items()})
    await session.commit()
    return {"applied": True, **before}


def report(result: dict[str, Any], *, what: str) -> str:
    applied = result.get("applied")
    lines = [f"{what}: {'DELETED' if applied else 'dry run, nothing was changed (add --apply to delete)'}"]
    for key, value in result.items():
        if key != "applied":
            lines.append(f"  {key:<24} {value:>8}")
    if not applied:
        lines.append("Shops' tokens would be deleted first, so no call can be made for any shop from that moment.")
    return "\n".join(lines)
