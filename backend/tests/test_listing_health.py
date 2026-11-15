"""Listing health (pipeline/listing_health.py, api/listing_health.py, workers/listing_fixes.py).

Each bucket and each Fix now rule; a selling listing left alone; the live-rewrite count; a preview and an approval before any write; the 50-listing cap; the read-back; Undo for 7
days; a listing changed on Etsy left alone; the allowance rule said before a rewrite; another account 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.api import deps
from app.compliance import trademarks
from app.core import allowance
from app.db.models import (
    AllowanceUse,
    AuditLog,
    EtsyConnection,
    ListingFix,
    ListingProfile,
    ListingPublication,
    SalesDaily,
    SalesSync,
    ShopListingCache,
    Tenant,
    UsageEvent,
)
from app.etsy.errors import EtsyError
from app.pipeline import etsy_text
from app.pipeline import listing_fixes as F
from app.pipeline import listing_health as H
from app.pipeline.llm import LLMResult, Usage
from app.workers import listing_fixes as jobs
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.test_api import client  # noqa: F401  (fixture)

GOOD_TAGS = ["hiking gear", "mountain lover", "camp tee", "outdoor top", "trail runner", "retro nature", "forest walk",
             "summit view", "park ranger", "weekend trek", "pine tree top", "nature lover", "alpine style"]


def listing(listing_id: int = 901, **over: Any) -> dict[str, Any]:
    return {
        "listing_id": listing_id, "state": "active", "title": "Retro Mountain Shirt, Green Cotton Hiking Top",
        "tags": list(GOOD_TAGS), "description": "A soft green cotton shirt with a retro mountain drawn across the chest. " * 4,
        "materials": ["cotton"], "images": [{"url_170x135": f"https://i.etsystatic.com/{i}.jpg"} for i in range(6)],
        "shop_section_id": 7, "taxonomy_id": 482, "url": f"https://www.etsy.com/listing/{listing_id}/x",
        **over,
    }


NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
REFERENCE = [{"property_id": 2, "property_name": "Sleeve length", "value_ids": [9], "values": ["Short"]}]
FULL = [{"property_id": 1, "property_name": "Primary color", "value_ids": [1], "values": ["Black"]}, *REFERENCE]
REQUIRED = [{"id": 1, "name": "Primary color"}]
#: A long keyword title of the kind that sells in these shops: 20 words, repeats, a gift phrase.
LONG = "Nurse Shirt, Funny Nurse Gift, RN Shirt, Nursing School Graduation Gift, ER Nurse Tee, Nurse Week Gift for Her"


def judge(row: dict[str, Any] | None = None, *, recent: int = 0, past: int = 0, known: bool = True, **more: Any) -> H.Verdict | None:
    kw: dict[str, Any] = {"properties": FULL, "required": REQUIRED, "reference_attributes": REFERENCE, "now": NOW}
    kw.update(more)
    return H.classify(row if row is not None else listing(), recent_units=recent, past_units=past, sales_known=known, **kw)


def ids(verdict: H.Verdict | None) -> set[str]:
    return {p.id for p in verdict.problems} if verdict else set()


# --- the buckets ------------------------------------------------------------------------------------------


def test_a_selling_listing_is_left_alone_however_its_title_and_tags_look() -> None:
    row = listing(title=LONG, tags=GOOD_TAGS[:4], materials=[], images=[{"url_170x135": "https://i.etsystatic.com/1.jpg"}], shop_section_id=None,
                  description="Short.")
    verdict = judge(row, recent=3, past=40, properties=FULL[:1])
    assert verdict.bucket == H.SELLING and verdict.problems == [] and verdict.suggestions == []
    assert verdict.note == "Changing the title or tags of a selling listing can lower its search position."
    assert not verdict.fixes  # nothing to preview for it


def test_a_selling_listing_moves_only_for_a_fix_now_risk_and_then_only_that_is_said() -> None:
    row = listing(title="DisneyWorld " + LONG[:100], tags=GOOD_TAGS[:4])
    verdict = judge(row, recent=5, past=50)
    assert verdict.bucket == H.FIX_NOW and ids(verdict) == {"brand"}  # no title or tag advice beside it
    assert all(p.level == H.FIX_NOW for p in verdict.problems) and verdict.note == H.SELLING_NOTE
    assert "tags_missing" not in ids(verdict) and "title_guidance" not in ids(verdict)


@pytest.mark.parametrize("recent", [1, 7, 120])
def test_no_title_or_tag_suggestion_is_ever_made_for_a_selling_listing(recent: int) -> None:
    for row in (listing(title=LONG), listing(tags=GOOD_TAGS[:2]), listing(tags=["christmas 2024", *GOOD_TAGS[:5]]),
                listing(title="Cute Shirt, Free Shipping, Gift for Her"), listing(title="Christmas Tree Shirt")):
        verdict = judge(row, recent=recent, past=recent, properties=FULL[:1])
        assert verdict.bucket == H.SELLING, row["title"]
        assert not ({"tags", "rewrite"} & verdict.fixes) and not verdict.suggestions


def test_a_listing_that_does_not_sell_with_a_one_click_fix_is_worth_improving() -> None:
    few = judge(listing(tags=GOOD_TAGS[:6]), past=3, tags_to_add=["lake house"])
    assert few.bucket == H.IMPROVE and ids(few) == {"tags_missing"} and few.problems[0].fixes == ("tags",)
    assert few.problems[0].sentence == "6 of 13 tags used. Etsy lets you add up to 13."
    no_reference_tags = judge(listing(tags=GOOD_TAGS[:6]))
    assert no_reference_tags.problems[0].fixes == ("rewrite",)
    attrs = judge(listing(), properties=FULL[:1])
    assert attrs.bucket == H.IMPROVE and ids(attrs) == {"attributes_missing"} and "Sleeve length" in attrs.problems[0].sentence
    title = judge(listing(title=LONG))
    assert title.bucket == H.IMPROVE and ids(title) == {"title_guidance"} and title.problems[0].fixes == ("rewrite",)
    assert "Etsy's Help suggests fewer than 15" in title.problems[0].sentence and title.problems[0].kind == H.ETSY


def test_no_sale_and_nothing_we_can_fix_is_left_quietly() -> None:
    verdict = judge(listing(materials=[], shop_section_id=None, description="Short."))
    assert verdict.bucket == H.NOTHING and verdict.problems == []  # materials, section, length: no longer rules


def test_a_long_keyword_listing_the_app_wrote_in_that_style_is_not_told_off() -> None:
    assert judge(listing(title=LONG), short_style=False).bucket == H.NOTHING


def test_a_listing_too_new_to_judge_is_quiet_unless_something_is_really_wrong() -> None:
    young = listing(tags=GOOD_TAGS[:3], original_creation_timestamp=int((NOW - timedelta(days=10)).timestamp()))
    verdict = judge(young)
    assert verdict.bucket == H.TOO_NEW and verdict.problems == [] and verdict.age_days == 10
    assert judge({**young, "title": "DisneyWorld Trip Tee"}).bucket == H.FIX_NOW
    old = {**young, "original_creation_timestamp": int((NOW - timedelta(days=31)).timestamp())}
    assert judge(old).bucket == H.IMPROVE


def test_the_windows_are_configurable() -> None:
    young = listing(original_creation_timestamp=int((NOW - timedelta(days=10)).timestamp()), tags=GOOD_TAGS[:3])
    assert judge(young, windows=H.Windows(new_days=7)).bucket == H.IMPROVE
    assert judge(young, windows=H.Windows(new_days=11)).bucket == H.TOO_NEW
    # The selling window is the caller's sum of units within it (api/listing_health.py::_sales), read from settings.
    from app.api import listing_health as api_health
    from app.core.config import get_settings

    settings = get_settings()
    old = (settings.listing_health_selling_days, settings.listing_health_new_days)
    try:
        settings.listing_health_selling_days, settings.listing_health_new_days = 90, 14
        assert api_health.windows() == H.Windows(selling_days=90, new_days=14)
    finally:
        settings.listing_health_selling_days, settings.listing_health_new_days = old


def test_without_read_sales_no_title_or_tag_change_is_suggested() -> None:
    verdict = judge(listing(title=LONG, tags=GOOD_TAGS[:3]), known=False)
    assert verdict.bucket == H.NOT_JUDGED and verdict.problems == [] and "not been read" in verdict.note
    assert judge(listing(title="DisneyWorld Tee"), known=False).bucket == H.FIX_NOW  # a real problem is still said


# --- each Fix now rule ------------------------------------------------------------------------------------


def test_fix_now_brand_is_the_apps_sentence_in_any_form() -> None:
    verdict = judge(listing(title="DisneyWorld Family Shirt", tags=["mick ey tee", *GOOD_TAGS[:12]]))
    assert sorted(verdict.brands) == ["Disney", "Mickey"] and ids(verdict) == {"brand"}
    assert trademarks.BRAND_WARNING.format(brand="Disney") in verdict.problems[0].sentence


@pytest.mark.parametrize("title", ["a" * 141, "Frog \U0001F600 Shirt", "Mom & Dad & Kids Shirt"])
def test_fix_now_a_title_etsy_rejects(title: str) -> None:
    verdict = judge(listing(title=title), recent=4, past=4)
    assert verdict.bucket == H.FIX_NOW and "title_rejected" in ids(verdict)


@pytest.mark.parametrize("tags", [[*GOOD_TAGS, "extra"], ["a tag far too long for etsy", *GOOD_TAGS[:12]], ["tag!", *GOOD_TAGS[:12]], ["-tee", *GOOD_TAGS[:12]]])
def test_fix_now_tags_etsy_rejects_and_the_tag_fix_keeps_every_other_tag(tags: list[str]) -> None:
    verdict = judge(listing(tags=tags), recent=2, past=2)
    assert verdict.bucket == H.FIX_NOW and ids(verdict) == {"tags_rejected"} and verdict.problems[0].fixes == ("fix_tags",)
    fixed = F.fixed_tags(tags)
    assert fixed is not None and not etsy_text.tag_errors(fixed) and not etsy_text.tags_starting_badly(fixed)
    assert all(t in fixed for t in GOOD_TAGS[:12])
    assert F.fixed_tags(GOOD_TAGS) is None


def test_fix_now_no_main_photo() -> None:
    assert ids(judge(listing(images=[]))) == {"main_photo"}
    assert ids(judge(listing(images=[{"listing_image_id": 1}]))) == {"main_photo"}
    assert judge(listing()).bucket == H.NOTHING  # photos with links: fine
    assert "main_photo" not in ids(judge({k: v for k, v in listing().items() if k != "images"}))  # not read: not judged


def test_fix_now_a_required_attribute_missing() -> None:
    verdict = judge(listing(), properties=REFERENCE, recent=1, past=1)
    assert verdict.bucket == H.FIX_NOW and ids(verdict) == {"required_attribute"} and "Primary color" in verdict.problems[0].sentence
    assert verdict.problems[0].fixes == ("edit",)  # the reference listing does not have it either
    filled_by_reference = judge(listing(), properties=REFERENCE, reference_attributes=FULL)
    assert filled_by_reference.problems[0].fixes == ("attributes",)
    assert judge(listing(), properties=None).bucket == H.NOTHING  # not read yet: not judged
    assert judge(listing(), properties=REFERENCE, required=None).bucket == H.NOTHING  # no profile knows the category


@pytest.mark.parametrize("state", ["inactive", "expired", "sold_out"])
def test_fix_now_a_listing_that_used_to_sell_and_stopped(state: str) -> None:
    verdict = judge(listing(state=state), past=12)
    assert verdict.bucket == H.FIX_NOW and ids(verdict) == {"stopped_selling"} and "12 sales" in verdict.problems[0].sentence
    assert judge(listing(state=state), past=0) is None  # never sold: not Listing health's
    assert judge(listing(state="draft"), past=5) is None


def test_fix_now_stray_characters() -> None:
    verdict = judge(listing(description="A soft tee.\n}]"), recent=2, past=2)
    assert verdict.bucket == H.FIX_NOW and ids(verdict) == {"stray_characters"}
    assert verdict.problems[0].sentence.startswith("Description has stray characters.") and verdict.problems[0].fixes == ("clean_text",)


# --- seasonal tags ----------------------------------------------------------------------------------------


def test_dated_holiday_tags_are_a_suggestion_only_for_a_listing_that_does_not_sell() -> None:
    row = listing(tags=["christmas 2024", "halloween 2026", "xmas 2026", *GOOD_TAGS[:10]])
    assert H.dated_tags(row["tags"], NOW) == ["christmas 2024", "halloween 2026"]  # Christmas 2026 has not come yet
    verdict = judge(row)
    assert verdict.bucket == H.IMPROVE and ids(verdict) == {"dated_tags"} and "season_planner" in verdict.problems[0].fixes
    assert judge(row, recent=1, past=1).bucket == H.SELLING
    seasonal = judge(listing(title="Christmas Tree Shirt, Green Cotton Top"))
    assert seasonal.bucket == H.NOTHING and seasonal.suggestions[0].id == "seasonal" and "Dec 25" in seasonal.suggestions[0].sentence


def test_the_fixture_shop_falls_into_the_buckets_the_docs_give() -> None:
    from tests import health_fixture as fx

    found: dict[str, int] = {}
    for item in fx.shop(NOW):
        text = F.text_of(item.payload)
        verdict = H.classify(item.payload, recent_units=fx.sales_units(item, within_days=60), past_units=fx.sales_units(item), sales_known=True,
                             properties=item.payload.get("_properties"), required=[fx.REQUIRED], reference_attributes=fx.REFERENCE_ATTRIBUTES,
                             tags_to_add=F.added_tags(text["title"], text["tags"], fx.REFERENCE_TAGS), now=NOW)
        bucket = verdict.bucket if verdict else "none"
        found[bucket] = found.get(bucket, 0) + 1
        if fx.sales_units(item, within_days=60):
            assert bucket in (H.SELLING, H.FIX_NOW), item.why
    assert found == {H.SELLING: 14, H.FIX_NOW: 7, H.IMPROVE: 5, H.NOTHING: 6, H.TOO_NEW: 4, "none": 1}


# --- what a fix proposes ------------------------------------------------------------------------------


def test_added_tags_keep_the_listings_own_and_skip_what_breaks_the_rules() -> None:
    title = "Retro Mountain Shirt"
    added = F.added_tags(title, ["hiking gear"], ["Hiking Gears", "retro mountain", "disney tee", "cute top", "-dash", "a" * 21, "camp tee", "camp tees", "trail top"])
    assert added == ["camp tee", "trail top"]
    assert F.added_tags(title, [f"t {i}" for i in range(13)], ["camp tee"]) == []


def test_missing_attributes_never_overwrite_a_value() -> None:
    reference = [{"property_id": 1, "property_name": "Style", "value_ids": [3], "values": ["Retro"]},
                 {"property_id": 2, "property_name": "Sleeve", "value_ids": [9], "values": ["Short"]},
                 {"property_id": 3, "property_name": "Empty", "value_ids": [], "values": []}]
    have = [{"property_id": 1, "property_name": "Style", "value_ids": [4], "values": ["Modern"]}]
    assert [m["property_id"] for m in F.missing_attributes(have, reference)] == [2]


@pytest.mark.parametrize("data", [
    {"title": "", "tags": ["camp tee"]},
    {"title": "Disney Shirt", "tags": ["camp tee"]},
    {"title": "Cute Shirt", "tags": ["camp tee"]},
    {"title": "Frog Shirt Frog Tee", "tags": ["camp tee"]},
    {"title": "Frog Shirt", "tags": ["disney tee"]},
])
def test_what_the_model_writes_must_meet_the_rules(data: dict) -> None:
    assert F.usable_rewrite(data) is None


def test_a_usable_rewrite_drops_only_the_tags_that_fail() -> None:
    out = F.usable_rewrite({"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "disney tee", "-dash", "camp tees", "retro mountain", "trail walk"]})
    assert out == {"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "trail walk"]}


# --- preview, approval, the cap ------------------------------------------------------------------------


class Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args))

    def named(self, function: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == function]


class Etsy:
    """A shop on the other end: records every write, and answers with what it holds."""

    def __init__(self, listings: dict[int, dict[str, Any]], properties: dict[int, list[dict]] | None = None) -> None:
        self.listings, self.properties = listings, properties or {}
        self.writes: list[tuple] = []
        self.keep_tags = True

    async def get_listing(self, listing_id: int, **_: Any) -> dict:
        return dict(self.listings[listing_id])

    async def get_listing_properties(self, shop_id: int, listing_id: int, **_: Any) -> dict:
        return {"results": [dict(p) for p in self.properties.get(listing_id, [])]}

    async def update_listing(self, shop_id: int, listing_id: int, *, updates: dict, **_: Any) -> dict:
        self.writes.append(("update", listing_id, dict(updates)))
        kept = dict(updates)
        if not self.keep_tags:
            kept.pop("tags", None)
        self.listings[listing_id].update(kept)
        return {}

    async def update_listing_property(self, shop_id: int, listing_id: int, property_id: int, *, value_ids=None, values=None, scale_id=None, **_: Any) -> dict:  # noqa: ANN001
        self.writes.append(("property", listing_id, property_id, value_ids, values))
        rest = [p for p in self.properties.get(listing_id, []) if p["property_id"] != property_id]
        self.properties[listing_id] = [*rest, {"property_id": property_id, "property_name": "x", "value_ids": value_ids or [], "values": values or []}]
        return {}

    async def delete_listing_property(self, shop_id: int, listing_id: int, property_id: int, **_: Any) -> dict:
        self.writes.append(("delete", listing_id, property_id))
        self.properties[listing_id] = [p for p in self.properties.get(listing_id, []) if p["property_id"] != property_id]
        return {}


def wire(monkeypatch, etsy: Etsy) -> dict:  # noqa: ANN001
    class Service:
        async def get_valid_access_token(self, session, connection):  # noqa: ANN001
            return "tok"

    async def shop_id(session, client, connection, kw):  # noqa: ANN001
        return connection.shop_id

    async def gated(ctx, function, arg, tenant_id, body):  # noqa: ANN001
        return await body()

    monkeypatch.setattr(jobs, "_build_client", lambda *a, **k: etsy)
    monkeypatch.setattr(jobs, "_connection_service", lambda settings: Service())
    monkeypatch.setattr(jobs, "_resolve_shop_id", shop_id)
    monkeypatch.setattr(jobs, "_run_gated", gated)
    return {}


async def world(client: AsyncClient, rows: list[dict] | None = None, *, reference: dict | None = None, sales: dict[int, int] | None = None, sales_read: bool = True) -> dict:  # noqa: F811
    queue = Queue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]
    tid = client.tenant_id  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        shop = EtsyConnection(tenant_id=tid, etsy_user_id=31, shop_id=31, shop_name="Frost Tees")
        s.add(shop)
        await s.flush()
        s.add(ShopListingCache(tenant_id=tid, connection_id=shop.id, listing_id=500, payload={
            "listing_id": 500, "state": "active", "taxonomy_id": 482, "title": "Reference Shirt", "tags": ["lake house", "trail walk", "disney tee"],
            "description": "x" * 200, "materials": ["cotton"], "images": [{}] * 6, "shop_section_id": 1,
        }))
        profile = ListingProfile(tenant_id=tid, connection_id=shop.id, name="Tees", reference_listing_id=500, content_template="apparel",
                                 cached_payload={"taxonomy_id": 482, "attributes": [{"property_id": 2, "property_name": "Sleeve", "value_ids": [9], "values": ["Short"]}], **(reference or {})})
        s.add(profile)
        for row in rows if rows is not None else [listing()]:
            s.add(ShopListingCache(tenant_id=tid, connection_id=shop.id, listing_id=row["listing_id"], payload=row))
        if sales_read:
            s.add(SalesSync(connection_id=shop.id, tenant_id=tid, state="complete"))
        today = datetime.now(timezone.utc).date()
        for listing_id, days_ago in (sales or {}).items():
            s.add(SalesDaily(connection_id=shop.id, listing_id=listing_id, day=today - timedelta(days=days_ago), tenant_id=tid,
                             units=1, orders=1, revenue_minor=2000, currency="USD"))
        await s.commit()
        return {"queue": queue, "shop": shop.id, "profile": profile.id, "tid": tid}


async def _preview(client, w, ids, kinds, **more) -> Any:  # noqa: ANN001, F811
    return await client.post("/api/listing-health/preview", json={"connection_id": str(w["shop"]), "listing_ids": ids, "kinds": kinds, **more})


async def test_the_page_puts_each_listing_in_one_bucket_fix_now_first_and_reads_attributes_for_those_shown(client: AsyncClient) -> None:  # noqa: F811
    rows = [listing(), listing(902, title="Cute Shirt, Free Shipping"), listing(903, state="draft", title="Draft"),
            listing(904, title=LONG, tags=GOOD_TAGS[:4]), listing(905, title="DisneyWorld Trip Tee"), listing(906, state="expired")]
    w = await world(client, rows, sales={904: 5})
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
    assert page["reading"] is False and page["sales_known"] is True and page["show"] == "to_do"
    assert page["counts"] == {"fix_now": 2, "worth_improving": 1, "selling": 1, "too_new": 0, "nothing_to_fix": 1, "not_judged": 0}
    assert page["total"] == 5  # with the reference listing (its "disney tee" tag: Fix now); the draft and the expired one that never sold are not listed
    assert [l["listing_id"] for l in page["listings"]] == [500, 905, 902]  # Fix now first; the selling one is not "to do"
    assert page["selling_days"] == 60 and page["new_days"] == 30
    fix_now = page["listings"][1]
    assert fix_now["bucket"] == "fix_now" and fix_now["problems"][0]["id"] == "brand" and fix_now["url"].startswith("https://www.etsy.com/listing/905")
    selling = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "selling"})).json()["listings"]
    assert [l["listing_id"] for l in selling] == [904] and selling[0]["problems"] == [] and selling[0]["recent_sales"] == 1
    assert selling[0]["note"] == "Changing the title or tags of a selling listing can lower its search position."
    text = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).text
    assert '"score"' not in text and '"average"' not in text and "%" not in text  # nothing that reads as a grade
    row = next(l for l in page["listings"] if l["listing_id"] == 902)
    assert row["profile_name"] == "Tees" and row["attributes_read"] is False
    assert w["queue"].named("read_listing_properties")
    # A listing page never writes.
    assert not w["queue"].named("apply_listing_fix")


async def test_without_read_sales_the_page_says_so_and_suggests_no_title_or_tag_change(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(902, title="Cute Shirt, Free Shipping"), listing(905, title="DisneyWorld Trip Tee")], sales_read=False)
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()
    assert page["sales_known"] is False and page["counts"]["fix_now"] == 2 and page["counts"]["worth_improving"] == 0
    assert next(l for l in page["listings"] if l["listing_id"] == 902)["bucket"] == "not_judged"
    res = (await _preview(client, w, [902], ["rewrite"], accept_rewrite=True)).json()
    assert res["fixes"] == [] and "not been read" in res["skipped"][0]["reason"]


async def test_a_selling_listing_gets_no_title_or_tag_fix_even_when_asked(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(904, title=LONG, tags=GOOD_TAGS[:4]), listing(905, title="DisneyWorld " + LONG[:80])], sales={904: 3, 905: 3})
    for kinds in (["tags"], ["rewrite"]):
        res = (await _preview(client, w, [904], kinds, accept_rewrite=True)).json()
        assert res["fixes"] == [] and "selling" in res["skipped"][0]["reason"], kinds
    # A Fix now risk on a selling listing: its own fix is offered.
    res = (await _preview(client, w, [905], ["rewrite"], accept_rewrite=True)).json()
    assert [f["listing_id"] for f in res["fixes"]] == [905]
    assert not w["queue"].named("apply_listing_fix")


async def test_a_copy_past_its_six_hours_is_not_scored_it_is_read_again(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        for row in (await s.execute(select(ShopListingCache))).scalars():
            row.fetched_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
    assert page["reading"] is True and page["listings"] == []
    assert w["queue"].named("sync_shop_listings")


async def test_a_preview_changes_nothing_on_etsy_and_only_approval_starts_a_write(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5], _properties=[])])
    res = await _preview(client, w, [901], ["tags", "attributes"])
    assert res.status_code == 200, res.text
    fix = res.json()["fixes"][0]
    assert fix["state"] == "proposed" and set(fix["kinds"]) == {"tags", "attributes"}
    assert fix["before"]["tags"] == GOOD_TAGS[:5] and fix["after"]["tags"] == [*GOOD_TAGS[:5], "lake house", "trail walk"]  # from the reference, no brand
    assert fix["after"]["attributes"][0]["property_id"] == 2 and fix["before"]["attributes"][0]["value_ids"] == []
    assert not w["queue"].named("apply_listing_fix")
    # Unconfirmed: refused, nothing started.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]]})).status_code == 422
    assert not w["queue"].named("apply_listing_fix")
    ok = await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    assert ok.status_code == 200 and ok.json()["started"] == 1
    assert w["queue"].named("apply_listing_fix") == [("apply_listing_fix", (fix["id"],))]
    # Approved once: pressing again starts nothing.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})).status_code == 409


async def test_at_most_50_live_listings_per_approval(client: AsyncClient) -> None:  # noqa: F811
    rows = [listing(1000 + i, tags=GOOD_TAGS[:5]) for i in range(51)]
    w = await world(client, rows)
    ids = [r["listing_id"] for r in rows]
    too_many = await _preview(client, w, ids, ["tags"])
    assert too_many.status_code == 422 and "At most 50" in too_many.text
    res = await _preview(client, w, ids[:50], ["tags"])
    assert len(res.json()["fixes"]) == 50
    extra = await _preview(client, w, ids[50:], ["tags"])
    all_ids = [f["id"] for f in res.json()["fixes"]] + [f["id"] for f in extra.json()["fixes"]]
    refused = await client.post("/api/listing-health/apply", json={"fix_ids": all_ids, "confirm": True})
    assert refused.status_code == 422 and "At most 50" in refused.text and not w["queue"].named("apply_listing_fix")
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": all_ids[:50], "confirm": True})).json()["started"] == 50
    undo = await client.post("/api/listing-health/undo", json={"fix_ids": all_ids, "confirm": True})
    assert undo.status_code == 422 and "At most 50" in undo.text


async def test_a_preview_past_six_hours_cannot_be_approved(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5])])
    fix = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    res = await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    assert res.json()["started"] == 0 and res.json()["expired"] == 1 and "preview again" in res.json()["message"]
    assert not w["queue"].named("apply_listing_fix")


async def test_two_profiles_that_fit_are_the_sellers_choice_and_the_choice_settles_only_that(client: AsyncClient) -> None:  # noqa: F811
    other_category = listing(902, tags=GOOD_TAGS[:5], taxonomy_id=99)
    w = await world(client, [listing(tags=GOOD_TAGS[:5]), other_category])
    async with client.sm() as s:  # type: ignore[attr-defined]
        second = ListingProfile(tenant_id=w["tid"], connection_id=w["shop"], name="Other tees", reference_listing_id=501, content_template="apparel", cached_payload={"taxonomy_id": 482})
        s.add(second)
        s.add(ShopListingCache(tenant_id=w["tid"], connection_id=w["shop"], listing_id=501, payload={"listing_id": 501, "state": "draft", "tags": ["river cabin"]}))
        await s.commit()
        second_id = second.id
    row = next(l for l in (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()["listings"] if l["listing_id"] == 901)
    assert row["profile_unclear"] is True and row["profile_id"] is None
    asked = (await _preview(client, w, [901], ["tags"])).json()
    assert asked["fixes"] == [] and "no profile" in asked["skipped"][0]["reason"]
    chosen = (await _preview(client, w, [901, 902], ["tags"], profile_id=str(second_id))).json()
    assert [f["listing_id"] for f in chosen["fixes"]] == [901]  # 902's category fits neither: nothing is copied into it
    assert chosen["fixes"][0]["after"]["tags"][-1] == "river cabin"


# --- the write, the read-back and Undo ----------------------------------------------------------------


async def _approved(client, w, row: dict, kinds=("tags",)) -> str:  # noqa: ANN001, F811
    fix = (await _preview(client, w, [row["listing_id"]], list(kinds))).json()["fixes"][0]
    await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    return fix["id"]


async def test_applying_writes_what_was_previewed_reads_it_back_and_keeps_the_copy(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5], _properties=[])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)}, {901: []})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row, ("tags", "attributes"))
    ctx = {"sessionmaker": client.sm}  # type: ignore[attr-defined]
    assert await jobs.apply_listing_fix(ctx, fix_id) == "applied"
    assert ("update", 901, {"tags": [*GOOD_TAGS[:5], "lake house", "trail walk"]}) in etsy.writes
    assert ("property", 901, 2, [9], ["Short"]) in etsy.writes
    assert all("state" not in w_[2] for w_ in etsy.writes if w_[0] == "update")  # a fix never changes a listing's state
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        assert fix.state == "applied" and fix.before["tags"] == GOOD_TAGS[:5]
        until = fix.undo_until if fix.undo_until.tzinfo else fix.undo_until.replace(tzinfo=timezone.utc)
        assert timedelta(days=6) < until - datetime.now(timezone.utc) <= timedelta(days=7)
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "listing.health_fix_applied"))).scalars().first() is not None
        cached = await s.get(ShopListingCache, (w["tid"], 901))
        assert len(cached.payload["tags"]) == 7  # the page shows it as it now is
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"]
    assert listed[0]["state"] == "applied" and listed[0]["can_undo"] is True


async def test_a_read_back_that_differs_is_said_and_undo_is_still_offered(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    etsy.keep_tags = False
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "not-kept"  # type: ignore[attr-defined]
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"][0]
    assert listed["state"] == "failed" and "did not keep the tags" in listed["note"] and listed["can_undo"] is True


async def test_a_listing_changed_on_etsy_after_the_preview_is_left_alone(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: {**row, "tags": ["the seller's own edit"]}})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "changed"  # type: ignore[attr-defined]
    assert etsy.writes == []
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        assert fix.state == "changed" and "left as it is" in fix.note and fix.written_at is None


async def test_a_listing_no_longer_active_is_left_alone(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: {**row, "state": "inactive"}})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "not-active"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_a_run_cut_short_repeats_without_writing_twice(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    target = [*GOOD_TAGS[:5], "lake house", "trail walk"]
    etsy = Etsy({901: {**row, "tags": target}})  # the first run got this far
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "applied"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_a_job_that_was_not_approved_does_nothing(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    wire(monkeypatch, etsy)
    fix = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix["id"]) == "not-approved"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_etsys_refusal_is_said_in_a_plain_sentence(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])

    class Refusing(Etsy):
        async def update_listing(self, *a: Any, **k: Any) -> dict:
            raise EtsyError("refused")

    wire(monkeypatch, Refusing({901: dict(row)}))
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "failed"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(ListingFix, uuid.UUID(fix_id))).note


async def _applied(client, w, row: dict, etsy: Etsy, monkeypatch, kinds=("tags",)) -> str:  # noqa: ANN001, F811
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row, kinds)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "applied"  # type: ignore[attr-defined]
    etsy.writes.clear()
    return fix_id


async def test_undo_writes_back_the_copy_and_clears_what_was_empty(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5], _properties=[])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)}, {901: []})
    fix_id = await _applied(client, w, row, etsy, monkeypatch, ("tags", "attributes"))
    assert (await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id]})).status_code == 422  # not confirmed
    res = await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert res.status_code == 200 and w["queue"].named("undo_listing_fix")
    assert await jobs.undo_listing_fix({"sessionmaker": client.sm}, fix_id) == "undone"  # type: ignore[attr-defined]
    assert ("update", 901, {"tags": GOOD_TAGS[:5]}) in etsy.writes and ("delete", 901, 2) in etsy.writes
    assert etsy.listings[901]["tags"] == GOOD_TAGS[:5] and etsy.properties[901] == []
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"][0]
    assert listed["state"] == "undone" and listed["can_undo"] is False


async def test_undo_restores_a_title_and_leaves_alone_what_the_seller_changed_since(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    fix_id = await _applied(client, w, row, etsy, monkeypatch)
    etsy.listings[901]["tags"] = ["the seller's later edit"]
    await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert await jobs.undo_listing_fix({"sessionmaker": client.sm}, fix_id) == "changed"  # type: ignore[attr-defined]
    assert etsy.writes == []
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(ListingFix, uuid.UUID(fix_id))).state == "undo_failed"


async def test_undo_lasts_7_days(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    fix_id = await _applied(client, w, row, Etsy({901: dict(row)}), monkeypatch)
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        fix.undo_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    res = await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert res.status_code == 409 and "7 days" in res.text and not w["queue"].named("undo_listing_fix")


async def test_the_copy_is_deleted_on_its_clock_and_with_the_shop(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    fix_id = await _applied(client, w, row, Etsy({901: dict(row)}), monkeypatch)
    now = datetime.now(timezone.utc)
    async with client.sm() as s:  # type: ignore[attr-defined]
        await purge_expired_rows(s, now=now)
        assert await s.get(ListingFix, uuid.UUID(fix_id)) is not None  # within its 7 days
        await purge_expired_rows(s, now=now + timedelta(days=8))
        assert await s.get(ListingFix, uuid.UUID(fix_id)) is None

async def test_a_preview_nobody_approved_goes_after_six_hours_and_everything_goes_with_the_shop(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5])])
    first = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(first["id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
        await purge_expired_rows(s, now=datetime.now(timezone.utc))
        assert await s.get(ListingFix, uuid.UUID(first["id"])) is None
        second = ListingFix(tenant_id=w["tid"], connection_id=w["shop"], listing_id=901, kinds=["tags"], state="applied", before={"tags": ["a"]}, after={"tags": ["a", "b"]})
        s.add(second)
        await s.commit()
        await purge_shop_etsy_content(s, w["shop"])
        await s.commit()
        assert await s.get(ListingFix, second.id) is None


# --- the rewrite and the allowance ---------------------------------------------------------------------

#: A title against the search guidance: a listing that does not sell may be rewritten.
FLAGGED = "Cute Retro Mountain Shirt, Green Cotton Top, Free Shipping"


class Model:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "trail walk", "forest hike"]}
        self.calls = 0

    async def complete_json(self, **kwargs: Any) -> LLMResult:
        self.calls += 1
        return LLMResult(data=self.data, usage=Usage(input_tokens=10, output_tokens=10, model="m"))


async def test_a_rewrite_must_be_asked_for_and_the_rule_is_said_first(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(title=FLAGGED)])
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert "rewrite rule" in check["rule"] and check["counted"] == 1 and check["free"] == 0  # the app never wrote it
    res = await _preview(client, w, [901], ["rewrite"])
    assert res.status_code == 422 and "uses your allowance" in res.text
    assert not w["queue"].named("write_listing_fix_text")
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(AllowanceUse))).scalars().first() is None


async def test_a_rewrite_becomes_a_preview_and_counts_only_when_written(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    w = await world(client, [listing(title=FLAGGED)])
    res = await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)
    fix = res.json()["fixes"][0]
    assert fix["state"] == "writing" and w["queue"].named("write_listing_fix_text")
    # Writing it does not approve it.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})).status_code == 409
    model = Model()
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: model)
    assert await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"]) == "written"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        assert row.state == "proposed" and row.after["title"] == model.data["title"] and row.before["title"] == FLAGGED
        uses = list((await s.execute(select(AllowanceUse))).scalars())
        assert [(u.kind, u.design) for u in uses] == [(allowance.GENERATION, "listing:901")]
    # A second rewrite of the same listing is a free one, while they last.
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert check["free"] == 1 and check["counted"] == 0


async def test_an_unusable_rewrite_counts_nothing_and_says_so(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    w = await world(client, [listing(title=FLAGGED)])
    fix = (await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)).json()["fixes"][0]
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: Model({"title": "Disney Shirt", "tags": ["camp tee"]}))
    assert await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"]) == "unusable"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        assert row.state == "could_not_write" and "Nothing was counted" in row.note
        assert (await s.execute(select(AllowanceUse))).scalars().first() is None


async def test_a_rewrite_past_the_allowance_is_refused_before_it_runs(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(title=FLAGGED)])
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = await s.get(Tenant, w["tid"])
        tenant.allowance_amount, tenant.allowance_period = 0, "monthly"
        await s.commit()
    res = await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)
    assert res.status_code == 429 and not w["queue"].named("write_listing_fix_text")




# --- live-listing rewrites and the listing counter (core/usage.py) -----------------------------------------


async def _rewrite(client: AsyncClient, w: dict, monkeypatch, model: "Model | None" = None, listing_id: int = 901) -> str:  # noqa: F811, ANN001
    fix = (await _preview(client, w, [listing_id], ["rewrite"], accept_rewrite=True)).json()["fixes"][0]
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: model or Model())
    return await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"])  # type: ignore[attr-defined]


async def _live_rows(client: AsyncClient) -> list[UsageEvent]:  # noqa: F811
    async with client.sm() as s:  # type: ignore[attr-defined]
        return list((await s.execute(select(UsageEvent))).scalars())


@pytest.fixture
def counter_on(monkeypatch):  # noqa: ANN001, ANN201
    from app.core import usage

    monkeypatch.setattr(usage, "mode", lambda: "on")


async def test_a_live_listing_rewrite_counts_one_and_a_second_in_the_period_counts_nothing(client: AsyncClient, monkeypatch, counter_on) -> None:  # noqa: F811, ANN001
    from app.core import usage

    w = await world(client, [listing(title=FLAGGED)])
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert check["counted"] == 1 and "once per allowance period" in check["rule"]
    assert await _rewrite(client, w, monkeypatch) == "written"
    rows = await _live_rows(client)
    assert len(rows) == 1 and rows[0].source == usage.LIVE_SOURCE and rows[0].listing_key.startswith(f"live:{w['shop']}:901:")
    assert rows[0].content_id is None and rows[0].connection_id == w["shop"]
    async with client.sm() as s:  # type: ignore[attr-defined]
        status = await allowance.status(s, await s.get(Tenant, w["tid"]))
        assert status.used == 1 and status.live_rewrites == 1
        fix = (await s.execute(select(ListingFix))).scalars().one()
        assert fix.allowance_kind == usage.LIVE_COUNTED
    # The same listing again this period: written, counted nothing.
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert check["counted"] == 0 and check["free"] == 1
    assert await _rewrite(client, w, monkeypatch) == "written"
    assert len(await _live_rows(client)) == 1
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(ListingFix))).scalars().one().allowance_kind == usage.LIVE_FREE
        assert (await usage.reconcile(s)).clean


async def test_a_new_period_counts_the_same_listing_once_again(client: AsyncClient, monkeypatch, counter_on) -> None:  # noqa: F811, ANN001
    w = await world(client, [listing(title=FLAGGED)])
    assert await _rewrite(client, w, monkeypatch) == "written"
    later = datetime.now(timezone.utc) + timedelta(days=35)
    monkeypatch.setattr(allowance, "_now", lambda: later)
    assert await _rewrite(client, w, monkeypatch) == "written"
    rows = await _live_rows(client)
    assert len(rows) == 2 and len({r.listing_key for r in rows}) == 2  # one per period


async def test_a_failed_ai_call_counts_nothing(client: AsyncClient, monkeypatch, counter_on) -> None:  # noqa: F811, ANN001
    class Broken:
        async def complete_json(self, **kwargs: Any) -> LLMResult:
            raise RuntimeError("provider down")

    w = await world(client, [listing(title=FLAGGED)])
    assert await _rewrite(client, w, monkeypatch, Broken()) == "unusable"  # type: ignore[arg-type]
    assert await _rewrite(client, w, monkeypatch, Model({"title": "Disney Shirt", "tags": ["camp tee"]})) == "unusable"
    assert await _live_rows(client) == []


async def test_a_listyro_listing_already_counted_this_period_adds_nothing(client: AsyncClient, monkeypatch, counter_on) -> None:  # noqa: F811, ANN001
    from app.core import usage

    w = await world(client, [listing(title=FLAGGED)])
    content_id, publication_id = uuid.uuid4(), uuid.uuid4()
    async with client.sm() as s:  # type: ignore[attr-defined]
        s.add(ListingPublication(id=publication_id, tenant_id=w["tid"], connection_id=w["shop"], content_id=None, former_content_id=None,
                                 etsy_listing_id=901, state="active", title=FLAGGED))
        s.add(UsageEvent(tenant_id=w["tid"], listing_key=str(content_id), content_id=content_id, publication_id=publication_id,
                         connection_id=w["shop"], at=datetime.now(timezone.utc), source="draft"))
        await s.commit()
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert check["counted"] == 0
    assert await _rewrite(client, w, monkeypatch) == "written"
    rows = await _live_rows(client)
    assert [r.source for r in rows] == ["draft"]  # nothing added
    # Counted in an earlier period: this period's rewrite counts once.
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(UsageEvent))).scalars().one()
        row.at = datetime.now(timezone.utc) - timedelta(days=70)
        await s.commit()
    assert await _rewrite(client, w, monkeypatch) == "written"
    assert sorted(r.source for r in await _live_rows(client)) == ["draft", usage.LIVE_SOURCE]


async def test_with_the_allowance_used_up_the_ai_is_not_called(client: AsyncClient, monkeypatch, counter_on) -> None:  # noqa: F811, ANN001
    w = await world(client, [listing(title=FLAGGED)])
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = await s.get(Tenant, w["tid"])
        tenant.allowance_amount, tenant.allowance_period = 1, "monthly"
        await s.commit()
    fix = (await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)).json()["fixes"][0]
    # Something else used the last one between the preview and the job.
    async with client.sm() as s:  # type: ignore[attr-defined]
        s.add(UsageEvent(tenant_id=w["tid"], listing_key="elsewhere", at=datetime.now(timezone.utc), source="ai_write"))
        await s.commit()
    model = Model()
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: model)
    assert await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"]) == "allowance"  # type: ignore[attr-defined]
    assert model.calls == 0
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        assert row.state == "could_not_write" and "nothing was counted" in row.note
    # And the page says so before anything is queued.
    res = await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)
    assert res.status_code == 429 and "used your 1 listings" in res.text
    assert len(await _live_rows(client)) == 1  # only the other one


async def test_scheduled_drafts_never_read_the_live_rewrite_count() -> None:
    """The allowance is read by the rewrite path only: no draft, go-live or schedule module imports it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    for name in ("workers/publish.py", "workers/schedule.py", "workers/plans.py", "etsy/publisher.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "live_counted_already" not in text and "record_live_rewrite" not in text, name


async def test_two_rewrites_of_the_same_listing_at_once_count_one(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Really at once, on Postgres: two rewrite jobs of one live listing finish together."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import usage
    from app.db.models import Base
    from tests.test_hard_cases import _drop_scratch_postgres, _scratch_postgres

    url = _scratch_postgres()
    monkeypatch.setattr(usage, "mode", lambda: "on")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        tenant = Tenant(email="race@example.com", password_hash="!", time_zone="UTC")
        s.add(tenant)
        await s.flush()
        shop = EtsyConnection(tenant_id=tenant.id, etsy_user_id=41, shop_id=41, shop_name="Race Tees")
        s.add(shop)
        await s.flush()
        s.add(ShopListingCache(tenant_id=tenant.id, connection_id=shop.id, listing_id=901, payload=listing(title=FLAGGED)))
        fixes = [ListingFix(tenant_id=tenant.id, connection_id=shop.id, listing_id=901, kinds=["rewrite"], state="writing",
                            before={"title": FLAGGED}, after={"title": FLAGGED}) for _ in range(2)]
        s.add_all(fixes)
        await s.commit()
        ids = [str(f.id) for f in fixes]
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: Model())
    results = await asyncio.gather(*(jobs.write_listing_fix_text({"sessionmaker": sm}, i) for i in ids))
    assert results == ["written", "written"]
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(UsageEvent)) == 1
        kinds = sorted((await s.execute(select(ListingFix.allowance_kind))).scalars())
        assert kinds == [usage.LIVE_COUNTED, usage.LIVE_FREE]
    await engine.dispose()
    _drop_scratch_postgres(url)


# --- another account ------------------------------------------------------------------------------------


async def test_another_accounts_shop_listing_and_fix_are_404(client: AsyncClient) -> None:  # noqa: F811
    from tests.auth_support import make_tenant

    w = await world(client)
    other = await make_tenant(client.sm, "other@example.com")  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        shop = EtsyConnection(tenant_id=other, etsy_user_id=77, shop_id=77, shop_name="Theirs")
        s.add(shop)
        await s.flush()
        s.add(ShopListingCache(tenant_id=other, connection_id=shop.id, listing_id=777, payload=listing(777, title="Their Secret Shirt")))
        fix = ListingFix(tenant_id=other, connection_id=shop.id, listing_id=777, kinds=["tags"], state="proposed", before={"tags": ["their tag"]}, after={"tags": ["their tag", "x"]})
        s.add(fix)
        await s.commit()
        theirs, their_fix = shop.id, fix.id
    assert (await client.get("/api/listing-health", params={"shop": str(theirs)})).status_code == 404
    assert (await client.post("/api/listing-health/preview", json={"connection_id": str(theirs), "listing_ids": [777], "kinds": ["tags"]})).status_code == 404
    assert (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(theirs), "listing_ids": [777]})).status_code == 404
    for path in ("apply", "undo", "discard"):
        assert (await client.post(f"/api/listing-health/{path}", json={"fix_ids": [str(their_fix)], "confirm": True})).status_code == 404
    # Their listing id through our own shop is simply not there.
    res = (await _preview(client, w, [777], ["tags"])).json()
    assert res["fixes"] == [] and "Their Secret" not in str(res)
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).text
    assert "Their Secret" not in page and "their tag" not in page
    assert not w["queue"].named("apply_listing_fix")


async def test_no_answer_carries_a_token_count_or_a_model(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    text = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).text
    for word in ("input_tokens", "claude", "cost_usd"):
        assert word not in text
