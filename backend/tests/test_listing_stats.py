"""Part D: what each app-published listing gets on Etsy, measured, never guessed.

Views and favourites once a day (as daily increases of Etsy's lifetime totals),
content versions with their reason, Analytics → Listings' views/favourites/
conversion, and the title-style comparison with sample sizes and 95% intervals.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    ContentVersion,
    EtsyConnection,
    GeneratedContent,
    Job,
    ListingPublication,
    ListingStatDaily,
    SalesDaily,
    ShopListingCache,
)
from app.etsy import categories
from app.etsy.publisher import publish_live
from app.pipeline import listing_traffic as lt
from app.pipeline import versions
from app.workers import gate
from app.workers import listing_stats as worker
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.test_pnl import MONTH
from tests.test_pnl import _seed as seed_month
from tests.test_publish_api import _shop, ctx  # noqa: F401  (fixture)
from tests.test_publisher import FakeEtsy, _publish
from tests.test_publisher import _seed as seed_publisher
from tests.test_workers_profiles import FakeService
from tests.test_workers_profiles import _seed as seed_shop

NOW = datetime.now(timezone.utc)


def test_the_daily_figure_is_the_increase_of_etsys_lifetime_total() -> None:
    assert worker.increase(130, 100, new=False) == 30
    assert worker.increase(90, 100, new=False) == 0  # never negative
    assert worker.increase(40, None, new=True) == 40  # went live within 48 hours: all of it is new
    assert worker.increase(4000, None, new=False) is None  # an old listing's first reading: unknown, not 4,000
    assert worker.increase(None, 100, new=False) is None  # Etsy gave no total


def test_the_read_is_upkeep_and_costs_one_request_per_100_listings() -> None:
    assert "read_listing_stats" in gate.UPKEEP and gate.JOB_COST["read_listing_stats"] == 10
    assert categories.category_of("read_listing_stats:abc") == "listing_stats"
    assert worker.daily_requests([1, 100, 101, 250, 0]) == 1 + 1 + 2 + 3


class StatsEtsy:
    def __init__(self, totals: dict[int, tuple[int, int]]) -> None:
        self.totals = totals
        self.batches: list[list[int]] = []

    async def get_listings_by_listing_ids(self, ids: list[int], **_: Any) -> dict[str, Any]:
        self.batches.append(list(ids))
        return {"results": [{"listing_id": i, "views": self.totals[i][0], "num_favorers": self.totals[i][1]}
                            for i in ids if i in self.totals]}


async def _publications(sm, conn_id, tenant_id, rows: list[tuple[int, str, datetime | None]]) -> None:
    async with sm() as s:
        for lid, state, live in rows:
            s.add(ListingPublication(tenant_id=tenant_id, connection_id=conn_id, etsy_listing_id=lid, state=state,
                                     published_at=live))
        await s.commit()


async def test_one_shop_is_read_once_a_day_using_todays_sync_first(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, _ = await seed_shop(async_sm, with_profile=False)
    async with async_sm() as s:
        conn_id = (await s.execute(select(EtsyConnection.id).where(EtsyConnection.tenant_id == tenant_id))).scalar_one()
        # Listing 3 was in today's shop sync: it costs no request.
        s.add(ShopListingCache(tenant_id=tenant_id, connection_id=conn_id, listing_id=3,
                               payload={"listing_id": 3, "views": 70, "num_favorers": 7}, fetched_at=NOW))
        await s.commit()
    await _publications(async_sm, conn_id, tenant_id, [
        (1, "active", NOW - timedelta(hours=5)),     # new: its total is its first day
        (2, "active", NOW - timedelta(days=40)),     # older: its first reading is a baseline
        (3, "active", NOW - timedelta(days=40)),
        (4, "draft", None),                           # never live: not read
        (5, "deleted_on_etsy", NOW - timedelta(days=9)),
    ])
    fake = StatsEtsy({1: (12, 2), 2: (4000, 90)})
    monkeypatch.setattr(worker, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(worker, "_build_client", lambda ctx, http, settings, **_: fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    assert await worker.read_listing_stats(ctx, str(conn_id)) == "stats:3:1"
    assert fake.batches == [[1, 2]]
    assert await worker.read_listing_stats(ctx, str(conn_id)) == "stats:0"  # once a day
    async with async_sm() as s:
        rows = {r.listing_id: r for r in (await s.execute(select(ListingStatDaily))).scalars()}
        assert (rows[1].views, rows[1].favorites) == (12, 2)
        assert (rows[2].views_total, rows[2].views) == (4000, None)
        assert rows[3].views_total == 70
        # The next day: the increase over the day before.
        published = await worker.published_listings(s, conn_id)
        conn = await s.get(EtsyConnection, conn_id)
        tomorrow = NOW.date() + timedelta(days=1)
        await worker.record(s, conn, tomorrow, {2: (4130, 95)}, published, NOW + timedelta(days=1))
        await s.commit()
        row = await s.get(ListingStatDaily, (conn_id, 2, tomorrow))
        assert (row.views, row.favorites) == (130, 5)


async def test_a_draft_carries_its_first_version_and_going_live_starts_it(async_sm: async_sessionmaker) -> None:
    await _publish(async_sm, FakeEtsy())
    async with async_sm() as s:
        version = (await s.execute(select(ContentVersion))).scalar_one()
        assert (version.reason, version.title_style, version.active_from, version.active_to) == ("generated", "long", None, None)
        assert version.tags and version.title
        publication = await s.get(ListingPublication, version.publication_id)
        content = await s.get(GeneratedContent, publication.content_id)
        conn = await s.get(EtsyConnection, publication.connection_id)
        content.approved = True
        await s.commit()
        job_id = (await s.execute(select(Job.id))).scalars().first()
        await publish_live(s, job_id=job_id, content=content, connection=conn, client=FakeEtsy(),
                           access_token="tok", tenant_limit=2000)
    async with async_sm() as s:
        version = (await s.execute(select(ContentVersion))).scalar_one()
        assert version.active_from is not None
        # Replacing the text closes it and starts a "replaced" version.
        publication = await s.get(ListingPublication, version.publication_id)
        await versions.replaced(s, publication, NOW, title="New", tags=["a"], description="d")
        await s.commit()
        rows = (await s.execute(select(ContentVersion).order_by(ContentVersion.created_at))).scalars().all()
        assert [r.reason for r in rows] == ["generated", "replaced"] and rows[0].active_to is not None
        assert rows[1].active_to is None and rows[1].active_from is not None


async def test_an_edit_in_the_app_marks_the_version_edited_and_the_style_is_recorded(async_sm: async_sessionmaker) -> None:
    _, _, content_id, _ = await seed_publisher(async_sm)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        assert versions.reason_for(content) == "generated" and versions.title_style(content) == "long"
        versions.mark_edited(content)
        content.attributes = {**content.attributes, "search": {"opening": "x"}}
        assert versions.reason_for(content) == "edited" and versions.title_style(content) == "short"


async def test_the_shop_takes_its_stats_and_versions_with_it_and_old_days_go(async_sm: async_sessionmaker) -> None:
    await _publish(async_sm, FakeEtsy())
    async with async_sm() as s:
        publication = (await s.execute(select(ListingPublication))).scalar_one()
        old = NOW.date() - timedelta(days=ListingStatDaily.RETENTION_DAYS + 1)
        for day in (old, NOW.date()):
            s.add(ListingStatDaily(connection_id=publication.connection_id, listing_id=publication.etsy_listing_id, day=day,
                                   tenant_id=publication.tenant_id, views_total=1, views=1))
        await s.commit()
        await purge_expired_rows(s)
        assert [r.day for r in (await s.execute(select(ListingStatDaily))).scalars()] == [NOW.date()]
        counts = await purge_shop_etsy_content(s, publication.connection_id)
        await s.commit()
        assert counts["content_versions"] == 1 and counts["listing_stat_days"] == 1
        assert (await s.execute(select(ContentVersion))).first() is None


async def test_the_month_shows_views_favourites_and_conversion_for_app_listings_only(ctx) -> None:  # noqa: F811
    shop, _, _ = await seed_month(ctx)
    tid = ctx["tenant_id"]
    async with ctx["sm"]() as s:
        live = datetime.combine(MONTH, datetime.min.time(), tzinfo=timezone.utc)
        for lid in (501, 777):
            s.add(ListingPublication(tenant_id=tid, connection_id=shop, etsy_listing_id=lid, state="active", published_at=live))
        for day in range(3):
            s.add(ListingStatDaily(connection_id=shop, listing_id=501, day=MONTH + timedelta(days=day), tenant_id=tid,
                                   views_total=100 * (day + 1), views=100, favorites=4))
        s.add(ListingStatDaily(connection_id=shop, listing_id=777, day=MONTH + timedelta(days=2), tenant_id=tid,
                               views_total=50, views=50, favorites=1))
        await s.commit()
    view = (await ctx["client"].get(f"/api/analytics/month?month={MONTH:%Y-%m}")).json()
    assert view["traffic_label"] == "listing views on Etsy, not search impressions"
    rows = {r["listing_id"]: r for r in view["listings"]}
    assert (rows[501]["views"], rows[501]["favorites"], rows[501]["views_days"]) == (300, 12, 3)
    assert rows[501]["conversion"] == rows[501]["orders"] / 300
    # Seen but not sold: in the table, converting at zero.
    assert rows[777]["units"] == 0 and rows[777]["views"] == 50 and rows[777]["conversion"] == 0
    # Not published with the app: no figure, and why.
    assert rows[502]["views"] is None and rows[502]["traffic_note"] == lt.NOT_APP


def _units(style: str, n: int, views: int, days: int = 10, favs: int = 2, orders: int = 1) -> list[lt.Unit]:
    return [lt.Unit(i, style, days=days, views=views + (i % 5), favorites=favs + (i % 3), orders=orders * (i % 2))
            for i in range(n)]


def test_a_style_with_too_few_listings_or_views_says_not_enough_data_yet() -> None:
    few = lt.summarise(_units("short", 29, 100), "short", orders_known=True)
    assert not few.enough and few.rates["views_per_listing_day"] is None
    assert few.note == "Not enough data yet: 29 of 30 listings and 2,956 of 1,000 views."
    quiet = lt.summarise(_units("long", 40, 10), "long", orders_known=True)
    assert not quiet.enough and "1,000 views" in quiet.note


def test_enough_data_gives_rates_with_95_percent_intervals_and_a_difference() -> None:
    short, long = _units("short", 40, 300), _units("long", 40, 100)
    a = lt.summarise(short, "short", orders_known=True)
    b = lt.summarise(long, "long", orders_known=False)
    assert a.enough and b.enough
    vpd = a.rates["views_per_listing_day"]
    assert vpd["low"] <= vpd["value"] <= vpd["high"] and abs(vpd["value"] - sum(u.views for u in short) / 400) < 1e-9
    assert b.rates["orders_per_view"] is None  # no sales read: no orders, not zero
    diff = lt.difference(a, b, {"short": short, "long": long})
    assert diff["views_per_listing_day"]["clear"] is True and diff["orders_per_view"] is None


async def test_the_comparison_takes_listings_published_in_the_period_by_their_style(ctx) -> None:  # noqa: F811
    tid = ctx["tenant_id"]
    async with ctx["sm"]() as s:
        shop = await _shop(s, tid)
        live = NOW - timedelta(days=20)
        for n in range(35):
            for style in ("short", "long"):
                lid = 10_000 + n * 2 + (style == "long")
                pub = ListingPublication(id=uuid.uuid4(), tenant_id=tid, connection_id=shop, etsy_listing_id=lid,
                                         state="active", published_at=live)
                s.add(pub)
                await s.flush()
                s.add(ContentVersion(tenant_id=tid, connection_id=shop, publication_id=pub.id, etsy_listing_id=lid,
                                     title="t", tags=[], title_style=style, reason="generated", active_from=live))
                for d in range(10):
                    s.add(ListingStatDaily(connection_id=shop, listing_id=lid, day=(live + timedelta(days=d)).date(),
                                           tenant_id=tid, views_total=0, views=6 if style == "short" else 3, favorites=1))
                s.add(SalesDaily(connection_id=shop, listing_id=lid, day=(live + timedelta(days=3)).date(), tenant_id=tid,
                                 units=1, orders=1, revenue_minor=100))
        await s.commit()
    res = (await ctx["client"].get("/api/analytics/title-styles?days=90")).json()
    styles = {x["style"]: x for x in res["styles"]}
    assert styles["short"]["listings"] == styles["long"]["listings"] == 35
    assert (styles["short"]["views"], styles["long"]["views"]) == (2100, 1050)
    assert styles["short"]["enough"] and styles["long"]["enough"]
    assert styles["short"]["views_per_listing_day"]["value"] == 6.0
    assert abs(styles["short"]["orders_per_view"]["value"] - 35 / 2100) < 1e-12 and res["orders_note"] is None
    assert res["label"] == "listing views on Etsy, not search impressions"
    assert (await ctx["client"].get("/api/analytics/title-styles?days=7")).status_code == 422


async def test_listings_published_before_versions_were_kept_are_left_out(ctx) -> None:  # noqa: F811
    tid = ctx["tenant_id"]
    async with ctx["sm"]() as s:
        shop = await _shop(s, tid)
        s.add(ListingPublication(tenant_id=tid, connection_id=shop, etsy_listing_id=1, state="active", published_at=NOW))
        await s.commit()
        out = await lt.compare_styles(s, shop, date.today() - timedelta(days=30), date.today(), date.today(), orders_known=True)
    assert [x["listings"] for x in out["styles"]] == [0, 0] and out["difference"] is None


def test_the_screens_and_docs_use_the_same_thresholds() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[2] / "frontend" / "lib" / "analyticsDefinitions.ts").read_text()
    assert f"STYLE_MIN_LISTINGS = {lt.MIN_LISTINGS};" in text and f"STYLE_MIN_VIEWS = {lt.MIN_VIEWS};" in text
