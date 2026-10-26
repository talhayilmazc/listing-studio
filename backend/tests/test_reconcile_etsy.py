"""Our records follow what the seller did on Etsy (etsy/reconcile.py; production report 2026-10-26).

A seller published drafts by hand in Shop Manager and deleted others; the app kept showing them as
drafts with a pending go-live. Checked before every go-live, by upkeep (at most every 6 hours per
listing) and by ``reconcile-etsy``; live means "published in Etsy by the seller", gone means
"deleted on Etsy", and nothing is ever written to Etsy for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import GroupPlan, Job, JobStatus, ListingPublication, PlannedSlot, Tenant
from app.etsy import reconcile
from app.etsy.errors import EtsyClientError
from app.ops import publish_report, reconcile_etsy
from app.workers import publish as publish_worker
from app.workers import reconcile as reconcile_worker
from app.workers.schedule import release_scheduled_publishes
from tests.test_end_to_end import _Httpx, _run_drafts, _send_drafts, _world

NOW = datetime.now(timezone.utc)
WRITES = ("POST ", "PUT ", "PATCH ", "DELETE ")


def _writes(requests: list[str]) -> list[str]:
    return [r for r in requests if r.startswith(WRITES)]


async def _drafted(tmp_path, test_settings: Settings, monkeypatch, listings: int) -> dict:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=listings)
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        w["pubs"] = {p.content_id: p.etsy_listing_id for p in (await s.execute(select(ListingPublication))).scalars()}
    return w


async def _due(w: dict, content_id: uuid.UUID) -> str:
    """Schedule this listing's draft for a minute ago, release it, and return the go-live job."""
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content_id))).scalar_one()
        pub.scheduled_for = NOW - timedelta(minutes=1)
        await s.commit()
    w["enqueued"].clear()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 1
    [job_id] = [a[1] for a in w["enqueued"] if a[0] == "run_publish_live_job"]
    return job_id


async def _slot(w: dict, content_id: uuid.UUID, state: str = "waiting") -> uuid.UUID:
    async with w["sm"]() as s:
        plan = GroupPlan(tenant_id=w["tenant"], batch_id=w["batch"], settings={})
        s.add(plan)
        await s.flush()
        slot = PlannedSlot(tenant_id=w["tenant"], plan_id=plan.id, content_id=content_id, connection_id=w["shops"][0],
                           draft_at=NOW + timedelta(hours=1), publish_at=NOW + timedelta(hours=3), state=state)
        s.add(slot)
        await s.commit()
        return slot.id


# --- before every go-live -------------------------------------------------------------------------


async def test_a_draft_the_seller_published_by_hand_is_recorded_live_and_nothing_is_sent(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    w["etsy"].listings[w["pubs"][content]]["state"] = "active"  # published in Shop Manager
    job_id = await _due(w, content)
    before = len(w["etsy"].requests)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    assert _writes(w["etsy"].requests[before:]) == [], "nothing is sent to Etsy for it"
    assert w["etsy"].requests[before:] == ["GET /listings/{id}"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalar_one()
    assert (pub.state, pub.published_by) == ("active", reconcile.SELLER_ON_ETSY) and pub.published_at is not None
    [item] = (await w["client"].get("/api/scheduled")).json()["items"]
    assert item["status"] == "published" and item["note"] == reconcile.PUBLISHED_NOTE
    [card] = (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()
    assert card["publications"][0]["published_by"] == reconcile.SELLER_ON_ETSY
    # "Published with the app" does not count it: the seller published it.
    assert (await w["client"].get("/api/account/allowance")).json()["published"] == 0
    await w["engine"].dispose()


async def test_a_draft_the_seller_deleted_is_marked_and_shown_and_its_slot_closes(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    gone, kept = w["contents"]
    del w["etsy"].listings[w["pubs"][gone]]  # deleted in Shop Manager
    slot = await _slot(w, gone)
    job_id = await _due(w, gone)
    before = len(w["etsy"].requests)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "nothing-to-publish"
    assert _writes(w["etsy"].requests[before:]) == []
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.former_content_id == gone))).scalar_one()
        job = await s.get(Job, uuid.UUID(job_id))
        closed = await s.get(PlannedSlot, slot)
    assert pub.state == "deleted_on_etsy" and pub.content_id is None and pub.scheduled_for is None
    assert job.status is JobStatus.cancelled and job.last_error == reconcile.DELETED_NOTE
    assert (closed.state, closed.note) == ("deleted_on_etsy", reconcile.DELETED_NOTE)
    cards = {c["id"]: c for c in (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()}
    assert cards[str(gone)]["publications"] == []
    assert [d["note"] for d in cards[str(gone)]["deleted_on_etsy"]] == [reconcile.DELETED_NOTE]
    assert cards[str(kept)]["deleted_on_etsy"] == []
    statuses = {i["status"] for i in (await w["client"].get("/api/scheduled")).json()["items"] if i["content_id"] == str(gone)}
    assert statuses == {"deleted"}
    await w["engine"].dispose()


async def test_a_draft_still_a_draft_goes_live_after_one_read(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    job_id = await _due(w, content)
    before = len(w["etsy"].requests)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    assert w["etsy"].requests[before:] == ["GET /listings/{id}", "PATCH /shops/{id}/listings/{id}"]
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalar_one()
    assert pub.state == "active" and pub.published_by is None
    await w["engine"].dispose()


async def test_a_listing_changed_on_etsy_is_not_published_over(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 1)
    [content] = w["contents"]
    w["etsy"].listings[w["pubs"][content]]["state"] = "inactive"  # live once, deactivated by the seller
    job_id = await _due(w, content)
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "nothing-to-publish"
    assert w["etsy"].listings[w["pubs"][content]]["state"] == "inactive"
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalar_one()
    assert pub.state == "draft" and pub.schedule_note == reconcile.changed_note("inactive")
    await w["engine"].dispose()


# --- upkeep ----------------------------------------------------------------------------------------


def _upkeep(w: dict, monkeypatch) -> None:
    monkeypatch.setattr(reconcile_worker, "httpx", _Httpx(w["etsy"]))
    monkeypatch.setattr("app.workers.profiles.get_cipher", publish_worker.get_cipher)


async def test_upkeep_follows_etsy_at_most_every_six_hours_and_never_writes(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 3)
    live, gone, draft = w["contents"]
    w["etsy"].listings[w["pubs"][live]]["state"] = "active"
    del w["etsy"].listings[w["pubs"][gone]]
    live_slot, gone_slot, draft_slot = await _slot(w, live), await _slot(w, gone), await _slot(w, draft)
    _upkeep(w, monkeypatch)
    w["enqueued"].clear()
    assert await reconcile_worker.reconcile_all_shops(w["ctx"]) == 1
    assert [a[0] for a in w["enqueued"]] == ["reconcile_listings"]
    before = len(w["etsy"].requests)
    result = await reconcile_worker.reconcile_listings(w["ctx"], str(w["shops"][0]))
    assert result.startswith("reconcile:3:")
    made = w["etsy"].requests[before:]
    assert _writes(made) == [] and made[0] == "GET /listings/batch"
    async with w["sm"]() as s:
        pubs = list((await s.execute(select(ListingPublication))).scalars())
        slots = {i: await s.get(PlannedSlot, i) for i in (live_slot, gone_slot, draft_slot)}
    by = {p.content_id or p.former_content_id: p for p in pubs}
    assert (by[live].state, by[live].published_by) == ("active", reconcile.SELLER_ON_ETSY)
    assert by[gone].state == "deleted_on_etsy" and by[draft].state == "draft"
    assert [slots[i].state for i in (live_slot, gone_slot, draft_slot)] == ["published_on_etsy", "deleted_on_etsy", "waiting"]
    assert by[draft].etsy_checked_at is not None
    # Within 6 hours: the remaining draft is not read again, and no shop is queued.
    before = len(w["etsy"].requests)
    assert await reconcile_worker.reconcile_listings(w["ctx"], str(w["shops"][0])) == "reconcile:0"
    assert w["etsy"].requests[before:] == []
    w["enqueued"].clear()
    assert await reconcile_worker.reconcile_all_shops(w["ctx"]) == 0
    await w["engine"].dispose()


async def test_a_draft_the_batch_read_leaves_out_is_read_alone_before_it_counts_as_deleted(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    w["etsy"].batch_hides_drafts = True
    _upkeep(w, monkeypatch)
    before = len(w["etsy"].requests)
    await reconcile_worker.reconcile_listings(w["ctx"], str(w["shops"][0]))
    assert w["etsy"].requests[before:] == ["GET /listings/batch", "GET /listings/{id}", "GET /listings/{id}"]
    async with w["sm"]() as s:
        assert {p.state for p in (await s.execute(select(ListingPublication))).scalars()} == {"draft"}
    await w["engine"].dispose()


async def test_read_states_rules() -> None:
    class Client:
        def __init__(self) -> None:
            self.single: list[int] = []

        async def get_listings_by_listing_ids(self, ids, **kw):  # noqa: ANN001, ANN201
            return {"results": [{"listing_id": 1, "state": "active"}]}

        async def get_listing(self, lid, **kw):  # noqa: ANN001, ANN201
            self.single.append(lid)
            if lid == 2:
                raise EtsyClientError(404)
            if lid == 3:
                raise httpx.ConnectError("no answer")
            return {"listing_id": lid, "state": "draft"}

    client = Client()
    reading = await reconcile.read_states(client, [1, 2, 3, 4, 5], {}, singles=3)
    assert reading.states == {1: "active", 2: reconcile.GONE, 4: "draft"}
    assert reading.unread == [3, 5] and reading.requests == 4 and client.single == [2, 3, 4]


def test_decide_only_follows_what_the_seller_did() -> None:
    draft = ListingPublication(state="draft", etsy_listing_id=1)
    assert reconcile.decide(draft, "active") == reconcile.PUBLISHED
    assert reconcile.decide(draft, reconcile.GONE) == reconcile.DELETED
    assert reconcile.decide(draft, "draft") is None and reconcile.decide(draft, "unknown") is None
    assert reconcile.decide(draft, "inactive") is None  # no schedule to withdraw
    draft.scheduled_for = NOW
    assert reconcile.decide(draft, "expired") == reconcile.WITHDRAWN
    assert reconcile.decide(ListingPublication(state="active", etsy_listing_id=1), "active") is None
    assert reconcile.decide(ListingPublication(state="deleted_on_etsy", etsy_listing_id=1), reconcile.GONE) is None


# --- reconcile-etsy and publish-report -------------------------------------------------------------


class _Client:
    """The fake Etsy's listings, read through what the CLI calls (getListingsByListingIds, getListing)."""

    def __init__(self, etsy) -> None:  # noqa: ANN001
        self.etsy = etsy

    async def get_listings_by_listing_ids(self, ids, **kw):  # noqa: ANN001, ANN201
        return {"results": [{"listing_id": i, "state": self.etsy.listings[i]["state"]} for i in ids if i in self.etsy.listings]}

    async def get_listing(self, lid, **kw):  # noqa: ANN001, ANN201
        if lid not in self.etsy.listings:
            raise EtsyClientError(404)
        return {"listing_id": lid, "state": self.etsy.listings[lid]["state"]}


class _Tokens:
    async def get_valid_access_token(self, session, shop):  # noqa: ANN001, ANN201
        return "token"


async def _rows(sm) -> list:  # noqa: ANN001
    async with sm() as s:
        return sorted((str(p.id), p.state, p.content_id, p.published_by, str(p.scheduled_for))
                      for p in (await s.execute(select(ListingPublication))).scalars())


async def test_the_cli_dry_run_changes_nothing_and_apply_changes_only_our_records(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 3)
    live, gone, _ = w["contents"]
    w["etsy"].listings[w["pubs"][live]]["state"] = "active"
    del w["etsy"].listings[w["pubs"][gone]]
    async with w["sm"]() as s:  # another account's draft, same listing id: never read or touched
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.commit()
    before, etsy_before = await _rows(w["sm"]), len(w["etsy"].requests)
    kwargs = {"client_factory": lambda shop, http: _Client(w["etsy"]), "token_service": _Tokens()}
    dry = await reconcile_etsy.run(w["sm"], "seller@example.com", **kwargs)
    assert "dry run" in dry and await _rows(w["sm"]) == before
    assert f"published in Etsy by the seller: 1 · {w['pubs'][live]}" in dry
    assert f"deleted on Etsy: 1 · {w['pubs'][gone]}" in dry and "as recorded (still a draft on Etsy): 1" in dry
    assert "token" not in dry.replace("tokens", "")
    applied = await reconcile_etsy.run(w["sm"], "seller@example.com", apply=True, **kwargs)
    assert "applied" in applied and "nothing was written to Etsy" in applied
    async with w["sm"]() as s:
        states = {p.etsy_listing_id: (p.state, p.published_by) for p in (await s.execute(select(ListingPublication))).scalars()}
    assert states[w["pubs"][live]] == ("active", reconcile.SELLER_ON_ETSY)
    assert states[w["pubs"][gone]][0] == "deleted_on_etsy"
    assert w["etsy"].requests[etsy_before:] == []  # the CLI's client is the read-only fake above
    assert "no account" in await reconcile_etsy.run(w["sm"], "nobody@example.com", **kwargs)
    nothing = await reconcile_etsy.run(w["sm"], "other@example.com", **kwargs)
    assert "== changes: none" in nothing
    # The report says both outcomes.
    report = await publish_report.report(w["sm"], "seller@example.com")
    assert "published in Etsy by the seller: 1" in report and "deleted on Etsy: 1" in report
    await w["engine"].dispose()


async def test_the_report_with_check_etsy_names_what_is_not_recorded_yet(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _drafted(tmp_path, test_settings, monkeypatch, 2)
    live, gone = w["contents"]
    w["etsy"].listings[w["pubs"][live]]["state"] = "active"
    del w["etsy"].listings[w["pubs"][gone]]
    out = await publish_report.report(w["sm"], "seller@example.com", check_etsy=True,
                                      client_factory=lambda shop, http: _Client(w["etsy"]), token_service=_Tokens())
    assert "published in Etsy by the seller: 1" in out and "deleted on Etsy: 1" in out
    assert out.count("not recorded yet (reconcile-etsy --apply records it)") == 2
    await w["engine"].dispose()
