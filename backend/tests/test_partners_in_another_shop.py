"""Production partners when a profile is used in another shop (production, 21 November 2026).

A profile from shop A was used in a new, empty shop B that has no production partner on Etsy. Etsy's API
cannot make one (only getShopProductionPartners exists), the link said "Etsy's API cannot create this; choose
one of this shop's" with nothing to choose, and every draft to B stopped.

* a shop with NO partner: its row says so, its drafts are made without ``production_partner_ids``;
* a shop with partners, none of the same name: a picker of the shop's own, one click, saved;
* a partner of the same name added on Etsy later is linked by itself when the seller refreshes;
* a scheduled go-live to a shop without one is held with a clear reason; the scheduling warns first;
* a shop that has the partner is as it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import EtsyConnection, GeneratedContent, Job, JobType, ListingProfile, ListingPublication, ProfileShopLink
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import links as L
from app.pipeline import profile_shops, shop_listing
from app.pipeline.targets import resolve_target
from app.workers import links as link_jobs
from app.workers.schedule import release_scheduled_publishes
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_profile_links import PAYLOAD, _ctx, _links, _setup_world, fake_etsy  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, FakeEtsy

NO_PARTNER_LINE = ("Frost Caps has no production partner on Etsy. Drafts are made without one; add it on Etsy "
                   "(Shop Manager → Settings → Production partners) before publishing, then press Refresh.")


async def _use_in_caps(world, fake_etsy, partners: list[dict]) -> dict:  # noqa: F811
    """The profile (its reference names "Print Co") used in Frost Caps, whose partners are ``partners``."""
    extra = await _setup_world(world)
    fake_etsy.lists[103].partners = partners
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [str(extra["a3"])]})
    assert res.status_code == 200, res.text
    assert await link_jobs._link_profile(_ctx(world), str(world["pa1"])) == "linked"
    return extra


async def _row(world, shop_name: str = "Frost Caps") -> dict:  # noqa: F811
    profile = (await world["a"].get("/api/profiles")).json()[0]
    return next(link for link in profile["links"] if link["shop_name"] == shop_name)


def test_the_plan_says_which_of_the_two_it_is() -> None:
    main = L.ShopSettings(partners=[{"production_partner_id": 801, "partner_name": "Print Co"}])
    none = L.plan_resource(L.PARTNERS, [801], main, L.ShopSettings())
    assert (none.reason, none.options, none.linked) == (L.NO_PARTNER, [], None)
    other = L.plan_resource(L.PARTNERS, [801], main, L.ShopSettings(partners=[{"production_partner_id": 3, "partner_name": "Other Printer"}]))
    assert other.reason == L.PARTNER_PICK and other.options == [{"id": 3, "label": "Other Printer"}]
    same = L.plan_resource(L.PARTNERS, [801], main, L.ShopSettings(partners=[{"production_partner_id": 9, "partner_name": " print co "}]))
    assert same.linked == [9] and same.how == "same name"


async def test_a_shop_with_no_partner_is_ready_says_so_and_gets_its_draft_without_one(world, fake_etsy) -> None:  # noqa: F811
    extra = await _use_in_caps(world, fake_etsy, [])
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    # Ready: everything a draft needs is there; the partner is a note, never a dead end.
    assert caps.status == "ready" and not caps.production_partner_ids
    assert (caps.notes or {}).get(L.PARTNERS) == L.NO_PARTNER
    row = await _row(world)
    assert (row["ready"], row["status"], row["partner"], row["partner_note"]) == (True, "ready", "none", NO_PARTNER_LINE)
    assert row["missing"] == []
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    shop = next(s for s in setup["shops"] if s["connection_id"] == str(extra["a3"]))
    partner = next(s for s in shop["settings"] if s["resource"] == "production_partners")
    assert partner["options"] == [] and partner["linked_ids"] == [] and shop["open"] == []

    # The draft: made, with this shop's own ids and no production partner (never the main shop's id).
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        target = await resolve_target(s, content, await s.get(EtsyConnection, extra["a3"]))
    assert target.ok, target.reason
    reference = L.shop_reference(PAYLOAD | {"taxonomy_id": 2078, "price": 25.0, "who_made": "i_did", "when_made": "made_to_order",
                                            "inventory_products": []}, target.link)
    assert reference["shop_missing"] == [] and reference["partner_missing"] is True and reference["production_partner_ids"] == []
    fake = FakeEtsy()
    async with world["sm"]() as s:
        job = Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft, payload={})
        s.add(job)
        await s.flush()
        await publish_content(
            s, job_id=job.id, content=await s.get(GeneratedContent, world["contents"][0]),
            connection=await s.get(EtsyConnection, extra["a3"]), sku="X1", thumbnail=PublishImage(b"t", "t.jpg"),
            fixed_image_ids=[], client=fake, access_token="tok", config=CONFIG, reference=reference, tenant_limit=2000,
        )
    assert "production_partner_ids" not in fake.last_listing
    assert fake.last_listing["shipping_profile_id"] == caps.shipping_profile_id
    # Sending is never refused for it, whoever the profile says made the item.
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = {**profile.cached_payload, "who_made": "someone_else"}
        assert shop_listing.partner_state(profile, caps) == shop_listing.PARTNER_OK


async def test_a_shop_with_other_partners_gets_a_picker_and_one_click_saves_it(world, fake_etsy) -> None:  # noqa: F811
    extra = await _use_in_caps(world, fake_etsy, [{"production_partner_id": 3, "partner_name": "Other Printer"},
                                                  {"production_partner_id": 4, "partner_name": "Second Printer"}])
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    assert caps.status == "ready" and (caps.notes or {}).get(L.PARTNERS) == L.PARTNER_PICK
    row = await _row(world)
    assert row["ready"] is True and row["partner"] == "choose" and "choose one of Frost Caps's" in row["partner_note"]
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    shop = next(s for s in setup["shops"] if s["connection_id"] == str(extra["a3"]))
    partner = next(s for s in shop["settings"] if s["resource"] == "production_partners")
    assert [o["label"] for o in partner["options"]] == ["Other Printer", "Second Printer"]

    url = f"/api/profiles/{world['pa1']}/setup/{extra['a3']}"
    assert (await world["a"].put(url, json={"resource": "production_partners", "ids": [999]})).status_code == 409
    res = await world["a"].put(url, json={"resource": "production_partners", "ids": [4]})
    assert res.status_code == 200, res.text
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    assert caps.production_partner_ids == [4] and caps.status == "ready" and L.PARTNERS not in (caps.notes or {})
    row = await _row(world)
    assert row.get("partner") is None and row.get("partner_note") is None
    # The choice stays when the shop is checked again.
    await world["a"].post(f"{url}/check")
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert (await _links(world, world["pa1"]))[extra["a3"]].production_partner_ids == [4]


async def test_a_partner_of_the_same_name_added_on_etsy_later_is_linked_by_a_refresh(world, fake_etsy) -> None:  # noqa: F811
    extra = await _use_in_caps(world, fake_etsy, [])
    assert (await _row(world))["partner"] == "none"
    queued: list[tuple] = []

    async def enqueue(function: str, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        queued.append((function, *args))

    ctx = {**_ctx(world), "enqueue": enqueue}
    # Nothing added yet: a refresh looks, finds no partner, and the row still says so.
    assert await link_jobs.relink_partners(ctx, connection_id=extra["a3"]) == 1
    assert queued == [("refresh_shop_links", str(extra["a3"]))]
    await link_jobs._refresh_shop_links(ctx, str(extra["a3"]))
    assert len(queued) == 1 and (await _row(world))["partner"] == "none"

    # The seller adds "Print Co" in Shop Manager, then refreshes the profile (or the shop's listings are read).
    fake_etsy.lists[103].partners = [{"production_partner_id": 3801, "partner_name": "Print Co"}]
    queued.clear()
    assert await link_jobs.relink_partners(ctx, profile_id=world["pa1"]) == 1
    await link_jobs._refresh_shop_links(ctx, str(extra["a3"]))
    assert ("link_profile", str(world["pa1"])) in queued
    assert await link_jobs._link_profile(ctx, str(world["pa1"])) == "linked"
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    assert caps.production_partner_ids == [3801] and caps.status == "ready" and L.PARTNERS not in (caps.notes or {})
    row = await _row(world)
    assert row["ready"] is True and row.get("partner") is None
    # Linked: nothing is looked for again.
    assert await link_jobs.relink_partners(ctx, connection_id=extra["a3"]) == 0

    # The Refresh button on the row does the same in one press.
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.connection_id == extra["a3"]))).scalar_one()
        link.production_partner_ids, link.notes = [], {L.PARTNERS: L.NO_PARTNER}
        await s.commit()
    assert (await world["a"].post(f"/api/profiles/{world['pa1']}/setup/{extra['a3']}/check")).status_code == 200
    await link_jobs._link_profile(ctx, str(world["pa1"]))
    assert (await _links(world, world["pa1"]))[extra["a3"]].production_partner_ids == [3801]


async def test_a_scheduled_go_live_to_a_shop_without_the_partner_is_held_with_the_reason(world, fake_etsy) -> None:  # noqa: F811
    extra = await _use_in_caps(world, fake_etsy, [])
    # Said before anything is scheduled: the drafts are made, going live there waits. Drafts alone: no warning.
    body = {"connection_id": str(extra["a3"]), "content_ids": [str(world["contents"][0])]}
    publish = await world["a"].post(f"/api/batches/{world['batch']}/send/preview", json={"when": "publish", **body})
    assert publish.status_code == 200, publish.text
    warned = [n for n in publish.json()["notes"] if "has no production partner on Etsy" in n]
    assert len(warned) == 1 and warned[0].startswith("Frost Caps") and "going live there is held" in warned[0]
    assert publish.json()["disabled"] is None and publish.json()["total"] == 1  # warned, never stopped
    drafts = (await world["a"].post(f"/api/batches/{world['batch']}/send/preview", json={"when": "drafts", **body})).json()
    assert not [n for n in drafts["notes"] if "production partner" in n] and drafts["total"] == 1
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with world["sm"]() as s:
        # A draft in Frost Caps (no partner) and one in Frost Tees (the main shop), both due now.
        for shop, listing in ((extra["a3"], 71), (world["a1"], 72)):
            s.add(ListingPublication(tenant_id=world["alice"], content_id=world["contents"][0], connection_id=shop,
                                     etsy_listing_id=listing, state="draft", scheduled_for=past))
        await s.commit()
        content = await s.get(GeneratedContent, world["contents"][0])
        gap = await profile_shops.partner_gap_for(s, content, extra["a3"])
        assert gap is not None and gap[1] == L.NO_PARTNER
        assert await profile_shops.partner_gap_for(s, content, world["a1"]) is None
    queued: list[tuple] = []

    async def enqueue(function: str, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        queued.append((function, *args))

    counts = await release_scheduled_publishes({**_ctx(world), "enqueue": enqueue})
    async with world["sm"]() as s:
        pubs = {p.connection_id: p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.etsy_listing_id.in_((71, 72))))).scalars()}
    held, live = pubs[extra["a3"]], pubs[world["a1"]]
    assert held.schedule_note == L.go_live_held("Frost Caps") and held.schedule_job_id is None
    assert "has no production partner on Etsy" in held.schedule_note and "Refresh" in held.schedule_note
    # The main shop's go-live is as it was.
    assert live.schedule_note is None and live.schedule_job_id is not None
    assert counts == {"released": 1, "held": 1}


async def test_a_shop_that_has_the_partner_is_as_it_was(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "group", "group_id": extra["group"]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    links = await _links(world, world["pa1"])
    assert links[world["a2"]].production_partner_ids == [2801] and links[extra["a3"]].production_partner_ids == [3801]
    for name in ("Frost Mugs", "Frost Caps"):
        row = await _row(world, name)
        assert row["ready"] is True and row.get("partner") is None and row.get("partner_note") is None
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        state = profile_shops.state_of(profile, links[world["a2"]])
        assert state.ready and state.partner is None
        reference = L.shop_reference(dict(profile.cached_payload), links[world["a2"]])
    assert reference["production_partner_ids"] == [2801] and "partner_missing" not in reference
    # A shop still missing a setting a draft needs is still not ready (only the partner stopped stopping it).
    bare = ProfileShopLink(connection_id=extra["a3"], status="incomplete", shipping_profile_id=1, notes={"return_policy": L.NOT_FOUND})
    state = profile_shops.state_of(ListingProfile(connection_id=world["a1"], cached_payload=PAYLOAD), bare)
    assert not state.ready and set(state.missing) == {"return_policy", "readiness_state"} and state.partner == L.NO_PARTNER


async def test_a_send_that_stopped_before_etsy_says_why_on_the_shop_row_with_the_action(world, fake_etsy) -> None:  # noqa: F811
    """The batch page's shop workspace: a draft job that failed, waits or is still being made is said on its row
    and on the shop's line at once, with the one action that fixes it."""
    from app.db.models import JobStatus

    extra = await _use_in_caps(world, fake_etsy, [])
    first, second = world["contents"][0], world["contents"][1]
    refusal = ("Frost Caps: Shipping profile: Etsy refused the copy of the shipping profile because of its origin postal code. "
               "Change its origin postal code on the shipping profile in Frost Tees's Shop Manager and press Refresh.")
    async with world["sm"]() as s:
        s.add(Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(first)}, last_error=refusal))
        s.add(Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft, status=JobStatus.queued,
                  payload={"content_id": str(second)}, paused_reason="shop_setup"))
        await s.commit()
    rows = (await world["a"].get(f"/api/batches/{world['batch']}/shops/{extra['a3']}/rows")).json()["rows"]
    by_id = {r["content_id"]: r for r in rows}
    failed, waiting = by_id[str(first)]["last_try"], by_id[str(second)]["last_try"]
    assert failed["status"] == "failed" and failed["reason"] == refusal
    assert failed["advice"]["action"] in ("set_up_shop", "profiles", "retry") and failed["advice"]["label"]
    assert waiting["status"] == "waiting" and waiting["reason"].startswith("Preparing Frost Caps's shipping, return and processing settings")
    shops = {s["name"]: s for s in (await world["a"].get(f"/api/batches/{world['batch']}/shops")).json()["shops"]}
    assert (shops["Frost Caps"]["failed"], shops["Frost Caps"]["failure"]) == (1, refusal)
    assert shops["Frost Caps"]["advice"]["label"] and shops["Frost Tees"]["failed"] == 0 and shops["Frost Tees"]["failure"] is None

    # A newer job says what is true now: being made, then nothing once it made the draft.
    async with world["sm"]() as s:
        retry = Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft, status=JobStatus.running,
                    payload={"content_id": str(first)}, created_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        s.add(retry)
        await s.commit()
        retry_id = retry.id
    row = next(r for r in (await world["a"].get(f"/api/batches/{world['batch']}/shops/{extra['a3']}/rows")).json()["rows"]
               if r["content_id"] == str(first))
    assert row["last_try"] == {"status": "working", "reason": None, "advice": None}
    async with world["sm"]() as s:
        (await s.get(Job, retry_id)).status = JobStatus.succeeded
        await s.commit()
    row = next(r for r in (await world["a"].get(f"/api/batches/{world['batch']}/shops/{extra['a3']}/rows")).json()["rows"]
               if r["content_id"] == str(first))
    assert row["last_try"] is None
    # Another account sees nothing of it.
    assert (await world["b"].get(f"/api/batches/{world['batch']}/shops/{extra['a3']}/rows")).status_code == 404
