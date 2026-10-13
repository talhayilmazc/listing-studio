"""Deleting every piece of stored Etsy data (app/ops/etsy_data.py).

``purge-etsy-data`` is the last step of docs/etsy-access-ended.md; ``scrub-etsy-data``
is the same wipe for a non-production database and refuses to run in production.
Both only count unless told to apply.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import cli
from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.db.base import Base
from app.db.models import (
    AdsDaily,
    Asset,
    AuditLog,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    LedgerDaily,
    LedgerSync,
    ListingProfile,
    ListingPublication,
    ListingStatDaily,
    SaleLine,
    SalesDaily,
    SalesSync,
    ShopListingCache,
    StatementImport,
    Tenant,
)
from app.etsy.connection import ConnectionService
from app.ops import etsy_data
from app.pipeline import profile_shops
from app.workers import retention
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


# --- one shop: disconnecting deletes its stored Etsy data at once -----------------------------


def test_the_disconnect_purge_names_every_table_that_holds_a_shops_etsy_data() -> None:
    """The home page, the Privacy Policy and the Terms say a disconnected shop's data is
    deleted at once. A new table of Etsy data must be added to the purge to keep that true."""
    purge = inspect.getsource(retention.purge_shop_etsy_content) + inspect.getsource(profile_shops.detach_shop)
    missing = [table.__name__ for table in etsy_data.ETSY_TABLES if table.__name__ not in purge]
    assert missing == []
    # And the purge is part of disconnecting itself, in the same transaction as the tokens.
    disconnect = inspect.getsource(ConnectionService.disconnect)
    call = "purge_shop_etsy_content(session, connection.id)"
    assert disconnect.count(call) == 1 and disconnect.count("session.commit()") == 1
    assert disconnect.index("access_token_enc = None") < disconnect.index(call) < disconnect.index("session.commit()")


async def test_disconnecting_a_shop_deletes_all_its_stored_etsy_data_at_once(async_sm: async_sessionmaker) -> None:
    async def with_statements(ids: tuple) -> tuple:
        tenant_id, shop_id = ids[0], ids[1]
        async with async_sm() as s:
            month = NOW.date().replace(day=1)
            s.add_all([
                StatementImport(connection_id=shop_id, month=month, tenant_id=tenant_id, rows=3, first_day=month, last_day=month, net_minor=1200),
                AdsDaily(connection_id=shop_id, day=NOW.date(), tenant_id=tenant_id, spend_minor=300),
                SalesSync(connection_id=shop_id, tenant_id=tenant_id),
                LedgerSync(connection_id=shop_id, tenant_id=tenant_id),
            ])
            await s.commit()
        return ids

    first = await with_statements(await _etsy_data(async_sm))
    async with async_sm() as s:  # the seed gives every shop the same Etsy ids: make room for a second one
        shop = await s.get(EtsyConnection, first[1])
        shop.etsy_user_id, shop.shop_id = 901, 4343
        await s.commit()
    other = await with_statements(await _etsy_data(async_sm))
    by_shop = [t for t in Base.metadata.sorted_tables if "connection_id" in t.c and t.name not in KEPT]
    assert len(by_shop) >= 15

    async def rows(shop_id) -> dict[str, int]:
        async with async_sm() as s:
            return {t.name: int(await s.scalar(select(func.count()).select_from(t).where(t.c.connection_id == shop_id)) or 0) for t in by_shop}

    before = await rows(first[1])
    held = {name for name, n in before.items() if n}
    # Listing cache, statistics, sales, ledger, statements, ads, the links to its drafts, its profile.
    assert held >= {"shop_listing_cache", "listing_stat_daily", "sales_daily", "sale_line", "sales_sync", "ledger_daily",
                    "ledger_sync", "statement_import", "ads_daily", "listing_publication", "listing_profile"}

    service = ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t")
    async with async_sm() as s:
        cancelled = await service.disconnect(s, await s.get(EtsyConnection, first[1]))
    assert cancelled["queued_jobs"] == 1

    # One call, one transaction: nothing of the shop's Etsy data is left to age out.
    assert await rows(first[1]) == {t.name: 0 for t in by_shop}
    async with async_sm() as s:
        shop = await s.get(EtsyConnection, first[1])
        assert (shop.access_token_enc, shop.refresh_token_enc, shop.status, shop.sections) == (None, None, ConnectionStatus.revoked, None)
        # The seller's own work stays: the upload and the text written in the app.
        content = await s.get(GeneratedContent, first[2])
        assert content is not None and content.title and await s.get(Asset, content.asset_id) is not None
    # Another account's shop is untouched.
    assert await rows(other[1]) == before


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
    with pytest.raises(etsy_data.Refused):
        await cli.seed_demo(async_sm, admin=True)
    with pytest.raises(etsy_data.Refused):
        await cli.remove_demo(async_sm)
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


async def test_removing_the_demo_takes_only_the_demo(async_sm: async_sessionmaker) -> None:
    """``seed-demo --remove`` finds its data by the demo's own addresses and touches nothing else."""
    from app.db.models import InviteRequest

    real = await _etsy_data(async_sm)

    class Files:
        deleted: list[str] = []

        def delete_prefix(self, prefix: str) -> None:
            self.deleted.append(prefix)

    async with async_sm() as s:
        s.add_all([
            Tenant(email="demo-shots@example.test", password_hash="x", is_admin=True),
            Tenant(email="demo-two@example.test", password_hash="x"),
            Tenant(email="demonstration@example.com", password_hash="x"),  # a real-looking account: stays
            InviteRequest(email="maker@example.test", shop="Northwind Tees", shop_count=1),
            InviteRequest(email="someone@example.com", shop="A real shop", shop_count=2),
        ])
        await s.commit()
    said = await cli.remove_demo(async_sm, Files())
    assert said.startswith("Removed 2 demo account(s)") and "1 sample invite request(s)" in said
    assert Files.deleted == ["demo-shots"]
    async with async_sm() as s:
        left = set((await s.execute(select(Tenant.email))).scalars())
        assert "demonstration@example.com" in left and not {e for e in left if e.endswith("@example.test")}
        assert [r.email for r in (await s.execute(select(InviteRequest))).scalars()] == ["someone@example.com"]
        # The account that was already there keeps everything.
        assert await s.get(Tenant, real[0]) is not None and int(await s.scalar(select(func.count()).select_from(SalesDaily))) == 1
    # Nothing to remove: says so, deletes no files.
    Files.deleted.clear()
    assert (await cli.remove_demo(async_sm, Files())).startswith("Removed 0 demo account(s) (none)") and Files.deleted == []


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
