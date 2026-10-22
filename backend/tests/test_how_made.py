"""How it's made (Etsy's Creativity Standards; pipeline/how_made.py, api/finish_in_etsy.py).

Etsy's API has no field for "How does your shop produce this item?" or "What tools are used to
make this item?". The seller answers both once per profile; review cards and "Where and when"
say what to tick in Etsy; "Finish in Etsy" lists every listing Listyro created until it is
marked done; a draft opened from there that went live asks "Mark done?" (never marked by
itself); and a weekly job watches Etsy's OpenAPI document for the fields. Nothing is sent today.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import EtsyConnection, ListingProfile, ListingPublication, Tenant
from app.pipeline import how_made
from tests.test_publish_api import _add_content, _batch_of, ctx  # noqa: F401  (fixture)


# --- the picker -----------------------------------------------------------------------------


def test_none_stands_alone_and_one_production_answer_is_needed() -> None:
    assert how_made.clean("altered", ["computerized"]) == ("altered", ["computerized"])
    assert how_made.clean("altered", ["computerized", "handheld"]) == ("altered", ["handheld", "computerized"])
    assert how_made.clean("natural", ["none"]) == ("natural", ["none"])
    assert how_made.clean(None, None) == (None, None)  # cleared
    with pytest.raises(how_made.NotValid, match="none"):
        how_made.clean("altered", ["none", "computerized"])
    with pytest.raises(how_made.NotValid):
        how_made.clean(None, ["computerized"])
    with pytest.raises(how_made.NotValid):
        how_made.clean("altered", [])
    with pytest.raises(how_made.NotValid):
        how_made.clean("printed", ["computerized"])
    with pytest.raises(how_made.NotValid):
        how_made.clean("altered", ["laser"])


async def _profile_id(ctx, content_id) -> uuid.UUID:  # noqa: F811
    from app.db.models import GeneratedContent

    async with ctx["sm"]() as s:
        return (await s.get(GeneratedContent, content_id)).listing_profile_id


async def test_profile_answers_are_saved_checked_and_said_on_the_card(ctx) -> None:  # noqa: F811
    content = await _add_content(ctx["sm"], ctx["tenant_id"])
    pid = await _profile_id(ctx, content)
    card = next(p for p in (await ctx["client"].get("/api/profiles")).json() if p["id"] == str(pid))
    assert card["how_made_line"] == "How it's made: not set" and card["how_made_set"] is False

    bad = await ctx["client"].patch(f"/api/profiles/{pid}", json={"how_made_production": "altered", "how_made_tools": ["none", "ai"]})
    assert bad.status_code == 422 and "none" in bad.json()["detail"]

    ok = await ctx["client"].patch(f"/api/profiles/{pid}", json={"how_made_production": "altered", "how_made_tools": ["computerized"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["how_made_line"] == "How it's made: shop alters it · computerized tools"
    assert ok.json()["how_made_set"] is True
    # Another change leaves them alone.
    renamed = await ctx["client"].patch(f"/api/profiles/{pid}", json={"name": "Tee 2"})
    assert renamed.json()["how_made_tools"] == ["computerized"]


async def test_apply_to_all_profiles(ctx) -> None:  # noqa: F811
    a = await _profile_id(ctx, await _add_content(ctx["sm"], ctx["tenant_id"]))
    b = await _profile_id(ctx, await _add_content(ctx["sm"], ctx["tenant_id"]))
    res = await ctx["client"].post("/api/profiles/how-made", json={"production": "made_from_scratch", "tools": ["handheld", "ai"]})
    assert res.status_code == 200 and res.json() == {"updated": 2}
    async with ctx["sm"]() as s:
        for pid in (a, b):
            p = await s.get(ListingProfile, pid)
            assert (p.how_made_production, p.how_made_tools) == ("made_from_scratch", ["handheld", "ai"])
        # Another account's profile cannot be named.
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        shop = EtsyConnection(tenant_id=other.id, etsy_user_id=901, shop_id=901)
        s.add(shop)
        await s.flush()
        theirs = ListingProfile(tenant_id=other.id, connection_id=shop.id, name="Theirs", reference_listing_id=1, confirmed=True)
        s.add(theirs)
        await s.commit()
        theirs_id = theirs.id
    assert (await ctx["client"].post("/api/profiles/how-made", json={
        "production": "altered", "tools": ["computerized"], "profile_ids": [str(theirs_id)]})).status_code == 404
    assert (await ctx["client"].post("/api/profiles/how-made", json={
        "production": "altered", "tools": ["none", "computerized"], "profile_ids": [str(a)]})).status_code == 422
    async with ctx["sm"]() as s:
        assert (await s.get(ListingProfile, theirs_id)).how_made_production is None


# --- Finish in Etsy -------------------------------------------------------------------------


async def _set(ctx, content_id, production, tools) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        p = await s.get(ListingProfile, await _profile_id(ctx, content_id))
        p.how_made_production, p.how_made_tools = production, tools
        await s.commit()


async def test_the_list_holds_every_created_listing_not_done_grouped_by_answers_then_shop(ctx) -> None:  # noqa: F811
    alters = [await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=1000 + i, listing_state=state)
              for i, state in enumerate(["draft", "draft", "active"])]
    for c in alters:
        await _set(ctx, c, "altered", ["computerized"])
    scratch = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=2000)
    await _set(ctx, scratch, "made_from_scratch", ["handheld"])
    unset = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=3000)
    done = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=4000, listing_state="active")
    gone = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=5000, listing_state="deleted_on_etsy")
    not_created = await _add_content(ctx["sm"], ctx["tenant_id"])  # no draft on Etsy
    async with ctx["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.etsy_listing_id == 4000))).scalar_one()
        how_made.mark_done(pub)
        await s.commit()

    body = (await ctx["client"].get("/api/finish-in-etsy")).json()
    assert (body["total"], body["drafts"], body["active"]) == (5, 4, 1)
    labels = [g["label"] for g in body["groups"]]
    assert labels == ["Shop alters it + computerized tools: 3 listings",
                      "Made from scratch + handheld tools: 1 listing",
                      "How it's made not set on the profile: 1 listing"]
    first = body["groups"][0]
    assert (first["production"], first["tools"], first["set"]) == ("An item my shop alters", "Computerized tools or machines", True)
    [shop] = first["shops"]
    assert [r["etsy_listing_id"] for r in shop["rows"]] == [1000, 1001, 1002]
    assert {r["state"] for r in shop["rows"]} == {"draft", "active"}
    # Both drafts and active listings open in Etsy's listing editor.
    assert all(r["editor_link"] == f"https://www.etsy.com/your/shops/me/listing-editor/edit/{r['etsy_listing_id']}" for r in shop["rows"])
    unset_group = body["groups"][2]
    assert unset_group["set"] is False and unset_group["production"] is None
    assert unset_group["profiles"] == [{"id": str(await _profile_id(ctx, unset)), "name": "Standard Tee"}]
    listed = {r["etsy_listing_id"] for g in body["groups"] for s in g["shops"] for r in s["rows"]}
    assert not {4000, 5000} & listed  # done, and deleted on Etsy
    del done, gone, not_created, scratch

    # The sidebar badge is the same count.
    attention = (await ctx["client"].get("/api/attention")).json()
    assert attention["nav"]["/batches/finish"] == 5


async def test_bulk_mark_done_takes_them_off_and_only_the_callers_own(ctx) -> None:  # noqa: F811
    for i in range(3):
        await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=6000 + i)
    rows = [r for g in (await ctx["client"].get("/api/finish-in-etsy")).json()["groups"] for s in g["shops"] for r in s["rows"]]
    res = await ctx["client"].post("/api/finish-in-etsy/done", json={"keys": [rows[0]["key"], rows[1]["key"]]})
    assert res.status_code == 200 and res.json() == {"done": 2, "total": 1}
    left = (await ctx["client"].get("/api/finish-in-etsy")).json()
    assert left["total"] == 1
    async with ctx["sm"]() as s:
        pub = await s.get(ListingPublication, uuid.UUID(rows[0]["key"]))
        assert how_made.is_done(pub)
        # The two Shop Manager ticks on the review card are the same record.
        assert set(pub.manual_done_keys()) >= set(how_made.DONE_KEYS)
    assert (await ctx["client"].post("/api/finish-in-etsy/done", json={"keys": [str(uuid.uuid4())]})).status_code == 404
    assert (await ctx["client"].post("/api/finish-in-etsy/done", json={"keys": []})).status_code == 422


async def test_ticks_on_the_review_card_count_here_too(ctx) -> None:  # noqa: F811
    content = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=6500)
    async with ctx["sm"]() as s:
        shop = (await s.execute(select(EtsyConnection.id))).scalars().first()
    for key in how_made.DONE_KEYS:
        r = await ctx["client"].put(f"/api/content/{content}/publications/{shop}/manual-steps/{key}", json={"done": True})
        assert r.status_code in (200, 204), r.text
    assert (await ctx["client"].get("/api/finish-in-etsy")).json()["total"] == 0


class _FakeEtsy:
    def __init__(self, live: set[int]) -> None:
        self.live = live
        self.asked: list[list[int]] = []

    async def get_listings_by_listing_ids(self, ids, **_kw):  # noqa: ANN001
        self.asked.append(list(ids))
        return {"results": [{"listing_id": i, "state": "active" if i in self.live else "draft"} for i in ids]}


async def test_a_draft_opened_from_the_list_that_went_live_asks_mark_done_never_marks_it(ctx, monkeypatch) -> None:  # noqa: F811
    from app.workers import how_made as worker

    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7000)
    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7001)
    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7002)  # never opened: not read
    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7003, listing_state="active")  # already live when opened
    rows = {r["etsy_listing_id"]: r for g in (await ctx["client"].get("/api/finish-in-etsy")).json()["groups"]
            for s in g["shops"] for r in s["rows"]}
    opened = await ctx["client"].post("/api/finish-in-etsy/opened", json={"keys": [rows[7000]["key"], rows[7001]["key"], rows[7003]["key"]]})
    assert opened.status_code == 200
    # Opening queues the read of that shop's opened drafts (at most once a minute).
    assert [c[0] for c in ctx["enqueuer"].calls].count("watch_opened_drafts") == 1

    etsy = _FakeEtsy(live={7000})

    class _Tokens:
        async def get_valid_access_token(self, _s, _c):  # noqa: ANN001
            return "token"

    monkeypatch.setattr(worker, "_connection_service", lambda _settings: _Tokens())
    monkeypatch.setattr(worker, "_build_client", lambda *_a, **_k: etsy)
    async with ctx["sm"]() as s:
        shop = (await s.execute(select(EtsyConnection.id))).scalars().first()
    assert await worker._watch({"sessionmaker": ctx["sm"]}, str(shop)) == "live:1 of 2"
    assert sorted(etsy.asked[0]) == [7000, 7001]

    body = (await ctx["client"].get("/api/finish-in-etsy")).json()
    rows = {r["etsy_listing_id"]: r for g in body["groups"] for s in g["shops"] for r in s["rows"]}
    assert rows[7000]["state"] == "active" and rows[7000]["went_live"] is True  # "Mark done?"
    assert rows[7001]["went_live"] is False and rows[7002]["went_live"] is False
    assert rows[7003]["went_live"] is False  # it was never a draft opened from here
    assert body["total"] == 4  # nothing was marked done by itself
    async with ctx["sm"]() as s:
        pub = await s.get(ListingPublication, uuid.UUID(rows[7000]["key"]))
        assert not how_made.is_done(pub) and pub.published_at is not None


async def test_the_cron_reads_only_shops_with_drafts_opened_in_the_last_three_days(ctx) -> None:  # noqa: F811
    from app.workers.how_made import watch_all_opened_drafts

    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7100)
    async with ctx["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalars().first()
        pub.how_made_opened_at = datetime.now(timezone.utc) - timedelta(days=4)
        await s.commit()
    queued: list = []

    async def enqueue(*args):  # noqa: ANN002
        queued.append(args)

    assert await watch_all_opened_drafts({"sessionmaker": ctx["sm"], "enqueue": enqueue}) == 0
    async with ctx["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalars().first()
        pub.how_made_opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()
    assert await watch_all_opened_drafts({"sessionmaker": ctx["sm"], "enqueue": enqueue}) == 1
    assert queued == [("watch_opened_drafts", str(pub.connection_id))]


# --- review cards and Where and when ---------------------------------------------------------


async def test_review_cards_and_where_and_when_say_what_to_tick(ctx) -> None:  # noqa: F811
    content = await _add_content(ctx["sm"], ctx["tenant_id"])
    batch = await _batch_of(ctx, content)
    [card] = (await ctx["client"].get(f"/api/batches/{batch}/content")).json()
    assert card["how_made_set"] is False
    assert card["how_made_line"].startswith('How it\'s made is not set on the profile "Standard Tee"')
    preview = (await ctx["client"].post(f"/api/batches/{batch}/send/preview", json={"when": "drafts"})).json()
    assert preview["how_made"] == [card["how_made_line"]]
    assert preview["disabled"] is None or "How it's made" not in preview["disabled"]  # never blocks

    await _set(ctx, content, "altered", ["computerized"])
    [card] = (await ctx["client"].get(f"/api/batches/{batch}/content")).json()
    assert card["how_made_set"] is True
    assert card["how_made_line"] == "After publishing, tick in Etsy: shop alters it · computerized tools"
    preview = (await ctx["client"].post(f"/api/batches/{batch}/send/preview", json={"when": "drafts"})).json()
    assert preview["how_made"] == ["After publishing, tick in Etsy: shop alters it · computerized tools"]


async def test_a_drafts_shop_manager_steps_say_the_exact_answers(ctx) -> None:  # noqa: F811
    content = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=7200)
    await _set(ctx, content, "altered", ["computerized", "ai"])
    [card] = (await ctx["client"].get(f"/api/batches/{await _batch_of(ctx, content)}/content")).json()
    steps = card["publications"][0]["manual_steps"]
    assert [s["detail"] for s in steps] == ["Tick in Etsy: An item my shop alters.",
                                           "Tick in Etsy: Computerized tools or machines, An AI generator."]


# --- sending, once Etsy publishes the fields ---------------------------------------------------


def test_nothing_is_sent_today_and_the_mapping_sends_once_turned_on(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.core import config

    profile = SimpleNamespace(how_made_production="altered", how_made_tools=["computerized"])
    assert how_made.draft_fields(profile) == {}  # off by default, and no field names
    monkeypatch.setattr(config.get_settings(), "etsy_how_its_made_fields", True)
    assert how_made.draft_fields(profile) == {}  # still no field names from Etsy's schema
    monkeypatch.setattr(how_made, "FIELD_NAMES", {"production": "production_method", "tools": "tools_used"})
    monkeypatch.setattr(how_made, "VALUES", {"production": {"altered": "altered"}, "tools": {"computerized": "computerized"}})
    assert how_made.draft_fields(profile) == {"production_method": "altered", "tools_used": ["computerized"]}
    monkeypatch.setattr(config.get_settings(), "etsy_how_its_made_fields", False)
    assert how_made.draft_fields(profile) == {}


def test_every_draft_path_builds_the_draft_with_the_mapping() -> None:
    """Generated, edited, hand-written and other-shop drafts all build createDraftListing's body in
    etsy/publisher.py; the How it's made fields are added there, after the reference's fields."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "etsy" / "publisher.py").read_text(encoding="utf-8")
    assert source.count("how_made.draft_fields(") == 1
    assert "create_draft_listing" in source


# --- the weekly spec watcher ------------------------------------------------------------------


def _spec(extra_create: dict | None = None) -> dict:
    listing = {"type": "object", "properties": {"listing_id": {}, "who_made": {}, "processing_min": {}, "production_partners": {}}}
    create = {"type": "object", "properties": {"title": {}, "who_made": {}, "production_partner_ids": {}, **(extra_create or {})}}
    return {
        "paths": {
            "/v3/application/shops/{shop_id}/listings": {"post": {
                "operationId": "createDraftListing",
                "requestBody": {"content": {"application/x-www-form-urlencoded": {"schema": create}}}}},
            "/v3/application/listings/{listing_id}": {"get": {
                "operationId": "getListing",
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ShopListing"}}}}}}},
            "/v3/application/shops/{shop_id}/listings/{listing_id}": {"patch": {
                "operationId": "updateListing",
                "requestBody": {"content": {"application/x-www-form-urlencoded": {"schema": {"type": "object", "properties": {"title": {}}}}}}}},
        },
        "components": {"schemas": {"ShopListing": listing}},
    }


def test_todays_spec_has_nothing_new_and_a_new_field_is_found() -> None:
    from app.workers import etsy_spec

    assert etsy_spec.new_how_made_fields(_spec()) == {}
    found = etsy_spec.new_how_made_fields(_spec({"production_method": {}, "tools_used": {}, "ai_disclosure": {}, "how_its_made": {},
                                                  "is_ai_generated": {}, "shipping_profile_id": {}}))
    assert found == {"createDraftListing": ["ai_disclosure", "how_its_made", "is_ai_generated", "production_method", "tools_used"]}
    text = etsy_spec.alert_text(found)
    assert "production_method" in text and "Nothing was changed" in text


async def test_the_watcher_tells_the_operator_once_and_changes_nothing(monkeypatch) -> None:
    from fakeredis import FakeAsyncRedis

    from app.core import alerts
    from app.etsy import guard
    from app.workers import etsy_spec

    sent: list[str] = []

    async def send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", send)
    monkeypatch.setattr(guard, "check", lambda _what: None)
    redis = FakeAsyncRedis()

    async def today(_url):  # noqa: ANN001
        return _spec()

    async def tomorrow(_url):  # noqa: ANN001
        return _spec({"how_its_made": {}})

    assert await etsy_spec.check_etsy_spec({"redis": redis}, fetch=today) == "nothing-new"
    assert await etsy_spec.check_etsy_spec({"redis": redis}, fetch=tomorrow) == "alerted"
    assert await etsy_spec.check_etsy_spec({"redis": redis}, fetch=tomorrow) == "already-told"
    assert len(sent) == 1 and "createDraftListing: how_its_made" in sent[0]
    assert how_made.FIELD_NAMES == {}  # nothing turned on by itself


async def test_the_watcher_never_fetches_while_etsy_calls_are_blocked(monkeypatch) -> None:
    from app.core import config
    from app.workers import etsy_spec

    monkeypatch.setattr(config.get_settings(), "etsy_calls_blocked", True)
    fetched: list = []

    async def fetch(url):  # noqa: ANN001
        fetched.append(url)
        return _spec()

    assert await etsy_spec.check_etsy_spec({}, fetch=fetch) == "unreadable"
    assert fetched == []
