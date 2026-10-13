"""Deleting every piece of stored Etsy data (app/ops/etsy_data.py).

``purge-etsy-data`` is the last step of docs/etsy-access-ended.md; ``scrub-etsy-data``
is the same wipe for a non-production database and refuses to run in production.
Both only count unless told to apply.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import cli
from app.core.config import Settings, set_settings_override
from app.db.base import Base
from app.db.models import (
    Asset,
    AuditLog,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    LedgerDaily,
    ListingProfile,
    ListingPublication,
    ListingStatDaily,
    SaleLine,
    SalesDaily,
    ShopListingCache,
    StatementImport,
    Tenant,
)
from app.ops import etsy_data
from tests.test_publisher import _seed

NOW = datetime.now(timezone.utc)

#: Tables that name a shop and are deliberately kept: the seller's own work and
#: settings (a batch, a group's choices), and the job and plan history.
KEPT = {"job", "listing_group_setting", "planned_slot", "upload_batch"}


def test_every_table_that_belongs_to_a_shop_is_wiped_or_deliberately_kept() -> None:
    """A new table with a ``connection_id`` must be added to ETSY_TABLES (or to KEPT, with a reason)."""
    by_shop = {table.name for table in Base.metadata.tables.values() if "connection_id" in table.c}
    wiped = {table.__tablename__ for table in etsy_data.ETSY_TABLES}
    assert by_shop - wiped == KEPT, sorted(by_shop - wiped - KEPT)
    # Etsy data that hangs off something else: the saved copies of listings (by job).
    assert "listing_snapshot" in wiped


async def _etsy_data(sm: async_sessionmaker) -> tuple:
    ids = await _seed(sm)
    tenant_id, shop_id, content_id, job_id = ids
    async with sm() as s:
        shop = await s.get(EtsyConnection, shop_id)
        shop.access_token_enc, shop.refresh_token_enc, shop.shop_name, shop.shop_id = b"a", b"r", "Real Shop Name", 4242
        shop.sections, shop.scopes = [{"id": 1, "title": "Tees"}], ["listings_r"]
        profile = ListingProfile(tenant_id=tenant_id, connection_id=shop_id, name="Tee", reference_listing_id=1,
                                 content_template="apparel", confirmed=True, cached_payload={"description": "from Etsy"})
        s.add(profile)
        await s.flush()
        (await s.get(GeneratedContent, content_id)).listing_profile_id = profile.id
        (await s.get(Job, job_id)).status = JobStatus.queued
        s.add_all([
            ShopListingCache(tenant_id=tenant_id, connection_id=shop_id, listing_id=1, payload={"title": "t"}, fetched_at=NOW),
            SalesDaily(connection_id=shop_id, listing_id=1, day=NOW.date(), tenant_id=tenant_id, units=2),
            SaleLine(connection_id=shop_id, transaction_id=1, tenant_id=tenant_id, receipt_id=10, listing_id=1, day=NOW.date(),
                     quantity=1, price_minor=1000, shipping_minor=0),
            LedgerDaily(connection_id=shop_id, day=NOW.date(), ledger_type="transaction", tenant_id=tenant_id, amount_minor=-65, entries=1),
            ListingStatDaily(connection_id=shop_id, listing_id=1, day=NOW.date(), tenant_id=tenant_id, views_total=5),
            ListingPublication(tenant_id=tenant_id, content_id=content_id, connection_id=shop_id, etsy_listing_id=77, state="draft"),
        ])
        await s.commit()
    return ids


async def test_a_dry_run_counts_and_changes_nothing(async_sm: async_sessionmaker) -> None:
    await _etsy_data(async_sm)
    report = await cli.purge_etsy_data(async_sm, apply=False)
    assert "dry run, nothing was changed" in report and "shops_with_tokens" in report
    async with async_sm() as s:
        assert int(await s.scalar(select(func.count()).select_from(SalesDaily))) == 1
        assert (await s.execute(select(EtsyConnection))).scalars().one().access_token_enc == b"a"
        assert (await s.execute(select(AuditLog))).scalars().all() == []


async def test_the_purge_deletes_all_etsy_data_and_keeps_the_sellers_own_work(async_sm: async_sessionmaker) -> None:
    tenant_id, shop_id, content_id, job_id = await _etsy_data(async_sm)
    report = await cli.purge_etsy_data(async_sm, apply=True)
    assert report.startswith("All stored Etsy data, every account: DELETED")
    async with async_sm() as s:
        for table in etsy_data.ETSY_TABLES:
            assert int(await s.scalar(select(func.count()).select_from(table)) or 0) == 0, table.__tablename__
        shop = await s.get(EtsyConnection, shop_id)
        # No token, so no call can be made; nothing left that names the shop on Etsy.
        assert (shop.access_token_enc, shop.refresh_token_enc, shop.status) == (None, None, ConnectionStatus.revoked)
        assert (shop.shop_name, shop.etsy_user_id, shop.shop_id, shop.sections, shop.scopes) == (None, None, None, None, None)
        assert (await s.get(Job, job_id)).status is JobStatus.cancelled
        # The account, its uploads and the text written in the app stay: they are the seller's, not Etsy's.
        assert await s.get(Tenant, tenant_id) is not None
        content = await s.get(GeneratedContent, content_id)
        assert content is not None and content.title and content.listing_profile_id is None
        assert await s.get(Asset, content.asset_id) is not None
        row = (await s.execute(select(AuditLog))).scalars().one()
        assert row.action == "etsy_data.purged" and row.details["sales_daily"] == 1 and row.details["shops_with_tokens"] == 1


async def test_the_scrub_refuses_production_and_makes_shop_names_anonymous_elsewhere(async_sm, test_settings: Settings) -> None:
    _, shop_id, _, _ = await _etsy_data(async_sm)
    set_settings_override(test_settings.model_copy(update={"app_env": "production"}))
    with pytest.raises(etsy_data.Refused, match="does not run when APP_ENV=production"):
        await cli.scrub_etsy_data(async_sm, apply=True)
    with pytest.raises(etsy_data.Refused):
        await cli.scrub_etsy_data(async_sm, apply=False)  # not even a count: the command is not for that server
    with pytest.raises(etsy_data.Refused):
        await cli.seed_demo(async_sm)
    async with async_sm() as s:
        assert (await s.get(EtsyConnection, shop_id)).shop_name == "Real Shop Name"  # untouched

    set_settings_override(test_settings.model_copy(update={"app_env": "development"}))
    assert "dry run" in await cli.scrub_etsy_data(async_sm, apply=False)
    report = await cli.scrub_etsy_data(async_sm, apply=True)
    assert "DELETED" in report
    async with async_sm() as s:
        shop = await s.get(EtsyConnection, shop_id)
        assert shop.shop_name == "Demo shop 1" and shop.access_token_enc is None and shop.etsy_user_id is None
        assert int(await s.scalar(select(func.count()).select_from(ShopListingCache))) == 0
        assert (await s.execute(select(AuditLog))).scalars().one().action == "etsy_data.scrubbed"


def test_the_commands_exist_and_default_to_a_dry_run(monkeypatch, capsys) -> None:
    """``purge-etsy-data`` with no flag must never delete: the runbook's last step is typed by hand."""
    called: dict[str, bool] = {}

    async def fake(sm, *, apply: bool) -> str:  # noqa: ANN001
        called["apply"] = apply
        return "report"

    monkeypatch.setattr(cli, "purge_etsy_data", fake)
    assert cli.main(["purge-etsy-data"]) == 0 and called == {"apply": False}
    assert cli.main(["purge-etsy-data", "--apply"]) == 0 and called == {"apply": True}
    monkeypatch.setattr(cli, "scrub_etsy_data", fake)
    assert cli.main(["scrub-etsy-data"]) == 0 and called == {"apply": False}
