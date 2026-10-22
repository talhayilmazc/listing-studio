"""How it's made (Etsy's Creativity Standards; pipeline/how_made.py).

Etsy's API has no field for "How does your shop produce this item?" or "What tools are used to
make this item?" (docs/etsy-how-its-made.md has the evidence). The owner's rule (2026-10-23): the
seller does nothing per listing for them, and nothing about them ever holds up a draft, a
schedule or a publish. So:

* the profile keeps the answers (validated; "none" alone) and says once where Shop Manager sets
  them; there is no per-listing list, step or badge;
* nothing is sent to Etsy for them today; the mapping that will send them is ready (the
  reference listing's own answers first, then the profile's), behind a setting;
* a draft, a publish and a scheduled run go on whether or not they are set; if Etsy ever refuses
  a publish over them, the seller is told exactly what to set, and the rest of the run goes on;
* a weekly job watches Etsy's OpenAPI document and tells the operator once if fields appear.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import EtsyConnection, GeneratedContent, Job, JobStatus, ListingProfile, ListingPublication, Tenant
from app.etsy.errors import HOW_MADE_FIX, EtsyClientError, explain
from app.etsy.manual_fields import MANUAL_FIELDS
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import how_made
from tests.test_publish_api import _add_content, _batch_of, ctx  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy
from tests.test_publisher import _seed as publisher_seed

REFUSAL = "How it's made is required before this listing can be activated."


# --- the picker ---------------------------------------------------------------------------------


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
    renamed = await ctx["client"].patch(f"/api/profiles/{pid}", json={"name": "Tee 2"})
    assert renamed.json()["how_made_tools"] == ["computerized"]  # another change leaves them alone


async def test_apply_to_all_profiles_and_another_accounts_profile_is_404(ctx) -> None:  # noqa: F811
    a = await _profile_id(ctx, await _add_content(ctx["sm"], ctx["tenant_id"]))
    b = await _profile_id(ctx, await _add_content(ctx["sm"], ctx["tenant_id"]))
    res = await ctx["client"].post("/api/profiles/how-made", json={"production": "made_from_scratch", "tools": ["handheld", "ai"]})
    assert res.status_code == 200 and res.json() == {"updated": 2}
    async with ctx["sm"]() as s:
        for pid in (a, b):
            p = await s.get(ListingProfile, pid)
            assert (p.how_made_production, p.how_made_tools) == ("made_from_scratch", ["handheld", "ai"])
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
    assert (await ctx["client"].patch(f"/api/profiles/{theirs_id}", json={
        "how_made_production": "altered", "how_made_tools": ["computerized"]})).status_code == 404
    async with ctx["sm"]() as s:
        assert (await s.get(ListingProfile, theirs_id)).how_made_production is None


# --- nothing per listing ---------------------------------------------------------------------------


async def test_no_listing_carries_a_step_list_or_a_line_about_them(ctx) -> None:  # noqa: F811
    """No tick list: a draft has no How it's made steps, the review card and Where and when no line,
    the sidebar no count, and there is no "Finish in Etsy" page."""
    assert MANUAL_FIELDS == ()
    content = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    batch = await _batch_of(ctx, content)
    [card] = (await ctx["client"].get(f"/api/batches/{batch}/content")).json()
    assert card["publications"][0]["manual_steps"] == []
    assert "how_made_line" not in card
    preview = (await ctx["client"].post(f"/api/batches/{batch}/send/preview", json={"when": "drafts"})).json()
    assert "how_made" not in preview
    attention = (await ctx["client"].get("/api/attention")).json()
    assert all("How it's made" not in i["label"] for i in attention["items"])
    assert (await ctx["client"].get("/api/finish-in-etsy")).status_code == 404


# --- nothing is sent to Etsy today; ready to send the day Etsy adds the field -----------------------


def _profile(production=None, tools=None, reference=None) -> SimpleNamespace:
    return SimpleNamespace(how_made_production=production, how_made_tools=tools,
                           cached_payload={how_made.REFERENCE_KEY: reference} if reference else {})


def _turn_on(monkeypatch) -> None:  # noqa: ANN001
    from app.core import config

    monkeypatch.setattr(config.get_settings(), "etsy_how_its_made_fields", True)
    monkeypatch.setattr(how_made, "FIELD_NAMES", {"production": "production_method", "tools": "tools_used"})
    monkeypatch.setattr(how_made, "VALUES", {"production": {"altered": "altered", "made_from_scratch": "scratch"},
                                             "tools": {"computerized": "computerized", "handheld": "hand"}})


def test_nothing_is_sent_today_and_the_mapping_sends_once_turned_on(monkeypatch) -> None:
    from app.core import config

    profile = _profile("altered", ["computerized"])
    assert how_made.draft_fields(profile) == {}  # off by default, and no field names
    monkeypatch.setattr(config.get_settings(), "etsy_how_its_made_fields", True)
    assert how_made.draft_fields(profile) == {}  # still no field names from Etsy's schema
    _turn_on(monkeypatch)
    assert how_made.draft_fields(profile) == {"production_method": "altered", "tools_used": ["computerized"]}
    monkeypatch.setattr(config.get_settings(), "etsy_how_its_made_fields", False)
    assert how_made.draft_fields(profile) == {}


def test_the_reference_listings_own_answers_come_first_then_the_profiles(monkeypatch) -> None:
    """Source order for the day the field exists: the reference listing's answers (read on refresh),
    else the profile's selection; an unreadable reference answer falls back, never fails."""
    _turn_on(monkeypatch)
    both = _profile("altered", ["computerized"], reference={"production": "made_from_scratch", "tools": ["handheld"]})
    assert how_made.in_use(both) == ("made_from_scratch", ["handheld"], "reference")
    assert how_made.draft_fields(both) == {"production_method": "scratch", "tools_used": ["hand"]}
    manual = _profile("altered", ["computerized"])
    assert how_made.in_use(manual) == ("altered", ["computerized"], "profile")
    broken = _profile("altered", ["computerized"], reference={"production": "bogus", "tools": ["laser"]})
    assert how_made.in_use(broken)[2] == "profile"
    assert how_made.in_use(_profile()) == (None, None, None)
    assert how_made.draft_fields(_profile()) == {}  # nothing set: nothing sent, nothing raised


def _how_made_like(field: str) -> bool:
    from app.workers.etsy_spec import KNOWN, PATTERN

    return bool(PATTERN.search(field)) and field not in KNOWN


@pytest.mark.parametrize("answers", [(None, None), ("altered", ["computerized"])], ids=["not set", "set"])
async def test_a_draft_sends_nothing_for_them_and_never_waits_on_them(async_sm, answers) -> None:
    """With answers set and with none: the draft is made, and createDraftListing carries no How
    it's made field (who_made / when_made, which are in Etsy's schema, are sent as always)."""
    _, conn_id, content_id, job_id = await publisher_seed(async_sm)
    async with async_sm() as s:
        conn = await s.get(EtsyConnection, conn_id)
        profile = ListingProfile(tenant_id=conn.tenant_id, connection_id=conn.id, name="Tee", reference_listing_id=1,
                                 confirmed=True, how_made_production=answers[0], how_made_tools=answers[1])
        s.add(profile)
        await s.commit()
        fake = FakeEtsy()
        result = await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=conn, sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"), client=fake, access_token="tok", config=CONFIG, reference=REFERENCE,
            theme="x", tenant_limit=2000, profile_id=profile.id,
        )
    assert result.listing_id
    sent = set(fake.last_listing)
    assert {"who_made", "when_made"} <= sent
    assert not {k for k in sent if _how_made_like(k)}, sent


def test_the_fields_this_app_sends_are_not_mistaken_for_how_its_made() -> None:
    for field in ("who_made", "when_made", "production_partner_ids", "processing_min", "is_supply", "materials"):
        assert not _how_made_like(field), field


# --- publishing never waits on them; a refusal is explained and the rest go on --------------------


def test_a_refusal_naming_them_says_exactly_what_to_set() -> None:
    refused = EtsyClientError(400, body=f'{{"error": "{REFUSAL}"}}', path="/application/shops/9/listings/5", method="PATCH")
    assert explain(refused) == HOW_MADE_FIX
    assert "Shop Manager" in HOW_MADE_FIX and "Editing options" in HOW_MADE_FIX
    assert "What tools are used to make this item?" in HOW_MADE_FIX
    other = EtsyClientError(400, body='{"error": "Invalid shipping_profile_id"}', path="/x", method="PATCH")
    assert explain(other) != HOW_MADE_FIX


async def test_a_scheduled_run_goes_on_after_one_listing_is_refused(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Two drafts due to go live; Etsy refuses the first (as if it wanted How it's made). The first
    fails with the plain sentence, the second goes live: nothing waits on the refused one."""
    from app.workers import publish as publish_worker
    from app.workers.schedule import release_scheduled_publishes
    from tests.test_end_to_end import _run_drafts, _send_drafts, _world

    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    await _run_drafts(w, await _send_drafts(w))
    first = min(w["etsy"].listings)
    w["etsy"].refuse_active[first] = REFUSAL
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    await release_scheduled_publishes(w["ctx"])
    live_jobs = [args[1] for args in w["enqueued"] if args[0] == "run_publish_live_job"]
    assert len(live_jobs) == 2
    for job in live_jobs:
        await publish_worker.run_publish_live_job(w["ctx"], job)
    states = {lid: d["state"] for lid, d in w["etsy"].listings.items()}
    assert states[first] == "draft" and list(states.values()).count("active") == 1
    async with w["sm"]() as s:
        jobs = {str(j.id): j for j in (await s.execute(select(Job).where(Job.id.in_([uuid.UUID(j) for j in live_jobs])))).scalars()}
    outcomes = sorted(j.status.value for j in jobs.values())
    assert outcomes == ["failed", "succeeded"], outcomes
    refused = next(j for j in jobs.values() if j.status is JobStatus.failed)
    assert refused.last_error and "Shop Manager" in refused.last_error, refused.last_error
    await w["engine"].dispose()


# --- the weekly spec watcher ------------------------------------------------------------------------


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
