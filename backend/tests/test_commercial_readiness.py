"""What Etsy's Commercial Access review asks of the app's behaviour.

* Click-through acceptance of the Terms and Privacy Policy, stored per account,
  asked again when the version changes.
* A seller can delete their account; its Etsy data goes at once and the rest by a job.
* Disconnecting a shop deletes its tokens, cancels what waited for it and says what.
* Analytics history has an explicit retention the operator sets.
* New shop connections can be paused, with the notice on the Shops page.
* The invite form: required fields, the operators' alert, the badge, the link to send.
* Every scope asked for has a stated reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.api import deps
from app.core import alerts, app_settings, terms
from app.core.config import get_settings
from app.core.crypto import TokenCipher
from app.core.sessions import SESSION_COOKIE
from app.db.models import (
    AiCall,
    AuditLog,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    GroupPlan,
    InviteRequest,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    SalesDaily,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.etsy import oauth
from app.etsy.connection import ConnectionService
from app.etsy.oauth import TokenResponse
from app.etsy.shops import ShopLimitReached
from app.pipeline import creativity
from app.pipeline.storage import LocalStorage
from app.workers import accounts as account_jobs
from app.workers.retention import purge_expired_rows
from tests.auth_support import open_session
from tests.test_admin import PASSWORD, world  # noqa: F401  (fixture)

NOW = datetime.now(timezone.utc)


class Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, function: str, *args, **kwargs) -> None:
        self.calls.append((function, *args))


@pytest.fixture()
def queue(world) -> Queue:  # noqa: F811
    q = Queue()
    world["app"].dependency_overrides[deps.get_enqueuer] = lambda: q
    return q


# --- click-through acceptance ---------------------------------------------------------------


async def _invite(world) -> str:  # noqa: F811
    res = await world["a"].post("/api/admin/invites", json={})
    assert res.status_code == 201, res.text
    return res.json()["code"]


async def test_an_account_cannot_be_created_without_the_tick_and_the_acceptance_is_stored(world) -> None:  # noqa: F811
    code = await _invite(world)
    form = {"email": "new@example.com", "password": PASSWORD, "invite_code": code}
    # The tick starts unticked; the server refuses whatever the form did.
    for body in (form, {**form, "accept_terms": False}):
        refused = await world["anon"].post("/api/account/register", json=body)
        assert refused.status_code == 422 and "Terms of Service and Privacy Policy" in refused.json()["detail"]
    async with world["sm"]() as s:
        assert (await s.execute(select(Tenant).where(Tenant.email == "new@example.com"))).scalar_one_or_none() is None

    made = await world["anon"].post("/api/account/register", json={**form, "accept_terms": True})
    assert made.status_code == 201, made.text
    assert (made.json()["terms_due"], made.json()["terms_accepted_version"]) == (False, terms.TERMS_VERSION)
    async with world["sm"]() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.email == "new@example.com"))).scalar_one()
        assert tenant.terms_version == terms.TERMS_VERSION and tenant.terms_accepted_at is not None


async def test_an_existing_account_is_asked_once_before_it_can_use_the_app(world) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    async with world["sm"]() as s:  # an account from before acceptance was recorded
        tenant = await s.get(Tenant, bob)
        tenant.terms_version, tenant.terms_accepted_at = None, None
        await s.commit()

    # Signed in, and told so; nothing of the app answers until it agrees.
    me = (await world["b"].get("/api/account/me")).json()
    assert me["terms_due"] is True and me["terms_version"] == terms.TERMS_VERSION
    for path in ("/api/batches", "/api/shops", "/api/profiles", "/api/quota"):
        refused = await world["b"].get(path)
        assert (refused.status_code, refused.json()["detail"]) == (403, terms.REQUIRED), path

    # Not agreeing, or agreeing to a text that is not the current one, changes nothing.
    assert (await world["b"].post("/api/account/terms", json={"accept": False, "version": terms.TERMS_VERSION})).status_code == 422
    assert (await world["b"].post("/api/account/terms", json={"accept": True, "version": "2020-01-01"})).status_code == 409
    assert (await world["b"].get("/api/batches")).status_code == 403

    done = await world["b"].post("/api/account/terms", json={"accept": True, "version": terms.TERMS_VERSION})
    assert done.status_code == 200 and done.json()["terms_due"] is False
    assert (await world["b"].get("/api/batches")).status_code == 200
    async with world["sm"]() as s:
        tenant = await s.get(Tenant, bob)
        assert tenant.terms_version == terms.TERMS_VERSION and tenant.terms_accepted_at is not None
        logged = (await s.execute(select(AuditLog).where(AuditLog.action == "account.terms_accepted"))).scalars().all()
        assert [row.details["version"] for row in logged] == [terms.TERMS_VERSION]
    # Once: agreeing again records nothing new.
    await world["b"].post("/api/account/terms", json={"accept": True, "version": terms.TERMS_VERSION})
    async with world["sm"]() as s:
        assert len((await s.execute(select(AuditLog).where(AuditLog.action == "account.terms_accepted"))).scalars().all()) == 1


async def test_a_new_version_asks_everyone_again(world, monkeypatch) -> None:  # noqa: F811
    assert (await world["b"].get("/api/batches")).status_code == 200
    monkeypatch.setattr(terms, "TERMS_VERSION", "2099-01-01")
    assert (await world["b"].get("/api/account/me")).json()["terms_due"] is True
    assert (await world["b"].get("/api/batches")).status_code == 403
    assert (await world["a"].get("/api/batches")).status_code == 403  # the admin too
    await world["b"].post("/api/account/terms", json={"accept": True, "version": "2099-01-01"})
    assert (await world["b"].get("/api/batches")).status_code == 200


# --- disconnecting a shop ------------------------------------------------------------------------


async def _waiting_work(world, tenant_id: uuid.UUID) -> uuid.UUID:  # noqa: F811
    """A queued draft job, a scheduled go-live and a planned draft for the account's shop."""
    async with world["sm"]() as s:
        shop = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant_id))).scalars().one()
        shop.access_token_enc, shop.refresh_token_enc = b"enc-access", b"enc-refresh"
        content = (await s.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == tenant_id))).scalars().first()
        s.add(Job(tenant_id=tenant_id, connection_id=shop.id, type=JobType.create_draft, status=JobStatus.queued,
                  payload={"content_id": str(content.id)}, batch_id=content.batch_id))
        s.add(ListingPublication(tenant_id=tenant_id, content_id=content.id, connection_id=shop.id, etsy_listing_id=4242,
                                 state="draft", scheduled_for=NOW + timedelta(days=2)))
        plan = GroupPlan(tenant_id=tenant_id, batch_id=content.batch_id, settings={})
        s.add(plan)
        await s.flush()
        s.add(PlannedSlot(tenant_id=tenant_id, plan_id=plan.id, content_id=content.id, connection_id=shop.id,
                          draft_at=NOW + timedelta(days=1), publish_at=NOW + timedelta(days=1, hours=2), state="waiting"))
        s.add(ShopListingCache(tenant_id=tenant_id, connection_id=shop.id, listing_id=9, payload={"title": "x"}, fetched_at=NOW))
        s.add(SalesDaily(connection_id=shop.id, listing_id=9, day=NOW.date(), tenant_id=tenant_id, units=1))
        await s.commit()
        return shop.id


async def test_disconnecting_deletes_the_tokens_cancels_what_waited_and_says_what(world) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    shop_id = await _waiting_work(world, bob)
    res = await world["b"].post(f"/api/shops/{shop_id}/disconnect")
    assert res.status_code == 200, res.text
    told = res.json()["disconnected"]
    # The job this test queued and the one the account already had waiting.
    assert (told["queued_jobs"], told["scheduled_publishes"], told["planned_drafts"]) == (2, 1, 1)
    assert res.json()["shops"] == []
    async with world["sm"]() as s:
        shop = await s.get(EtsyConnection, shop_id)
        # The tokens are gone at once: no call can be made for the shop.
        assert (shop.access_token_enc, shop.refresh_token_enc, shop.status) == (None, None, ConnectionStatus.revoked)
        jobs = (await s.execute(select(Job).where(Job.connection_id == shop_id))).scalars().all()
        assert [j.status for j in jobs] == [JobStatus.cancelled] * 2 and all("disconnected" in j.last_error for j in jobs)
        slot = (await s.execute(select(PlannedSlot).where(PlannedSlot.connection_id == shop_id))).scalars().one()
        assert slot.state == "cancelled" and "disconnected" in slot.note
        # Everything read from Etsy for it is deleted.
        for table in (ShopListingCache, SalesDaily, ListingPublication):
            assert (await s.execute(select(table).where(table.connection_id == shop_id))).scalars().all() == []
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "shop.disconnected"))).scalars().one()
        assert (row.details["queued_jobs"], row.details["planned_drafts"]) == (2, 1)
    # Another account's shop is not there to disconnect.
    other = await _waiting_work(world, world["admin"].tenant_id)
    assert (await world["b"].post(f"/api/shops/{other}/disconnect")).status_code == 404


# --- deleting the account ---------------------------------------------------------------------------


async def test_a_seller_deletes_their_own_account(world, queue: Queue, tmp_path) -> None:  # noqa: F811
    bob, admin = world["bob"].tenant_id, world["admin"].tenant_id
    shop_id = await _waiting_work(world, bob)
    async with world["sm"]() as s:
        s.add(AiCall(tenant_id=bob, at=NOW, day=NOW.date(), purpose="content", model="m", ok=True, input_tokens=10, output_tokens=5))
        await s.commit()
    storage = LocalStorage(tmp_path)
    storage.put(f"{bob}/batch/processed/a.jpg", b"pixels")
    storage.put(f"{admin}/batch/processed/a.jpg", b"pixels")

    # The account's own password, and an explicit confirmation.
    assert (await world["b"].post("/api/account/delete", json={"password": "wrong", "confirm": True})).status_code == 403
    assert (await world["b"].post("/api/account/delete", json={"password": PASSWORD})).status_code == 422
    res = await world["b"].post("/api/account/delete", json={"password": PASSWORD, "confirm": True})
    assert res.status_code == 200, res.text
    assert res.json() == {"shops_disconnected": 1, "queued_jobs": 2, "scheduled_publishes": 1, "planned_drafts": 1, "within_days": 30}
    assert ("delete_account", str(bob)) in queue.calls

    # At once: the shop is disconnected with its Etsy data, and the account cannot be used.
    async with world["sm"]() as s:
        shop = await s.get(EtsyConnection, shop_id)
        assert shop.access_token_enc is None and shop.status is ConnectionStatus.revoked
        assert (await s.execute(select(SalesDaily).where(SalesDaily.tenant_id == bob))).scalars().all() == []
        assert (await s.get(Tenant, bob)).deletion_requested_at is not None
    assert (await world["b"].get("/api/account/me")).status_code in (401, 403)
    again = await world["anon"].post("/api/account/login", json={"email": "bob@example.com", "password": PASSWORD})
    assert again.status_code == 403 and again.json()["detail"] == account_jobs.BEING_DELETED

    # Then the job: the uploads, then the account and everything that belongs to it.
    assert await account_jobs.delete_account({"sessionmaker": world["sm"], "storage": storage, "redis": world["redis"]}, str(bob)) == "deleted"
    assert not storage.exists(f"{bob}/batch/processed/a.jpg") and storage.exists(f"{admin}/batch/processed/a.jpg")
    async with world["sm"]() as s:
        assert await s.get(Tenant, bob) is None
        for table in (UploadBatch, GeneratedContent, ListingProfile, EtsyConnection, Job):
            assert (await s.execute(select(table).where(table.tenant_id == bob))).scalars().all() == [], table.__name__
        # The other account is untouched.
        assert await s.get(Tenant, admin) is not None
        assert (await s.execute(select(UploadBatch).where(UploadBatch.tenant_id == admin))).scalars().all()
        # Kept: the record that it happened (counts, no content, no address) and our own cost figures, unlinked.
        logged = {row.action: row for row in (await s.execute(select(AuditLog))).scalars()}
        assert {"account.deletion_requested", "account.deleted", "shop.disconnected"} <= set(logged)
        assert "bob@example.com" not in str([row.details for row in logged.values()])
        assert logged["account.deleted"].details["by"] == "seller" and logged["account.deleted"].target_tenant_id is None
        call = (await s.execute(select(AiCall))).scalars().one()
        assert call.tenant_id is None and call.input_tokens == 10
    # The address is free again; a second run has nothing to do.
    assert await account_jobs.delete_account({"sessionmaker": world["sm"], "storage": storage}, str(bob)) == "waiting"


async def test_a_lost_job_is_caught_by_the_sweep_and_a_running_job_is_waited_for(world, queue: Queue, tmp_path) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    await world["b"].post("/api/account/delete", json={"password": PASSWORD, "confirm": True})
    async with world["sm"]() as s:
        job = (await s.execute(select(Job).where(Job.tenant_id == bob))).scalars().first()
        job.status = JobStatus.running  # a draft that was already being made
        await s.commit()
        job_id = job.id
    ctx = {"sessionmaker": world["sm"], "storage": LocalStorage(tmp_path)}
    assert await account_jobs.finish_account_deletions(ctx) == 0  # it finishes its step first
    async with world["sm"]() as s:
        (await s.get(Job, job_id)).status = JobStatus.succeeded
        await s.commit()
    assert await account_jobs.finish_account_deletions(ctx) == 1
    async with world["sm"]() as s:
        assert await s.get(Tenant, bob) is None


async def test_the_only_admin_cannot_delete_the_account(world, queue: Queue) -> None:  # noqa: F811
    res = await world["a"].post("/api/account/delete", json={"password": PASSWORD, "confirm": True})
    assert res.status_code == 409 and "only admin" in res.json()["detail"]


# --- analytics history: an explicit retention -----------------------------------------------------------


async def test_analytics_history_is_kept_for_the_months_the_operator_sets(world) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    assert (await world["a"].get("/api/admin/analytics-retention")).json() == {"months": 24, "default_months": 24}
    assert (await world["anon"].get("/api/meta")).json()["analytics_retention_months"] == 24
    async with world["sm"]() as s:
        shop = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == bob))).scalars().one()
        for months in (1, 13, 23, 25):
            s.add(SalesDaily(connection_id=shop.id, listing_id=1, day=(NOW - timedelta(days=months * 30.5)).date(), tenant_id=bob, units=1))
        await s.commit()
        await purge_expired_rows(s)
        assert len((await s.execute(select(SalesDaily))).scalars().all()) == 3  # the 25-month-old day went

    # Shortened in Admin: applied at the next run, audited, and what the policy shows.
    assert (await world["a"].put("/api/admin/analytics-retention", json={"months": 12})).json()["months"] == 12
    assert (await world["anon"].get("/api/meta")).json()["analytics_retention_months"] == 12
    async with world["sm"]() as s:
        await purge_expired_rows(s)
        assert len((await s.execute(select(SalesDaily))).scalars().all()) == 1
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "app.analytics_retention_changed"))).scalars().one()
        assert (row.details["previous"], row.details["new"]) == (24, 12)
    for bad in (0, 61, "x"):
        assert (await world["a"].put("/api/admin/analytics-retention", json={"months": bad})).status_code == 422
    # Not an admin: the setting is not there.
    assert (await world["b"].put("/api/admin/analytics-retention", json={"months": 1})).status_code == 404
    assert (await world["b"].get("/api/admin/analytics-retention")).status_code == 404


# --- new shop connections paused ----------------------------------------------------------------------------


async def test_new_shop_connections_can_be_paused_and_the_shops_page_says_so(world) -> None:  # noqa: F811
    bob = world["bob"].tenant_id
    shops = (await world["b"].get("/api/shops")).json()
    assert (shops["connections_paused"], shops["pause_notice"], shops["slots"]["can_add"]) == (False, None, True)  # off by default

    assert (await world["b"].put("/api/admin/connections-paused", json={"on": True})).status_code == 404
    on = await world["a"].put("/api/admin/connections-paused", json={"on": True})
    assert on.json()["on"] is True and on.json()["notice"] == app_settings.PAUSE_NOTICE
    shops = (await world["b"].get("/api/shops")).json()
    assert shops["connections_paused"] is True and shops["slots"]["can_add"] is False
    assert shops["pause_notice"] == (
        "New shop connections are paused for now. Your existing shops keep working."
    )
    assert len(shops["shops"]) == 1  # the existing shop keeps working

    service = ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t")
    async with world["sm"]() as s:
        # A shop the account does not have: refused, nothing written.
        with pytest.raises(ShopLimitReached) as err:
            await service.save_from_tokens(s, bob, TokenResponse(access_token="777.abc", refresh_token="r", expires_in=3600), ["listings_r"])
        assert err.value.scope == "paused"
        # Its connected shop can still reconnect (to grant a permission): that adds no shop.
        mine = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == bob))).scalars().one()
        again = await service.save_from_tokens(
            s, bob, TokenResponse(access_token=f"{mine.etsy_user_id}.abc", refresh_token="r", expires_in=3600), ["listings_r", "transactions_r"])
        assert again.id == mine.id and "transactions_r" in again.scopes

    await world["a"].put("/api/admin/connections-paused", json={"on": False})
    assert (await world["b"].get("/api/shops")).json()["slots"]["can_add"] is True
    async with world["sm"]() as s:
        changes = (await s.execute(select(AuditLog).where(AuditLog.action == "app.connections_paused_changed"))).scalars().all()
        assert [row.details["new"] for row in changes] == [True, False]


# --- the invite form -----------------------------------------------------------------------------------------

FORM = {"email": "Maker@Example.com", "shop": "etsy.com/shop/makertees", "shop_count": 3, "note": "new designs weekly"}


async def test_an_invite_request_needs_the_shop_and_the_count_and_the_operators_are_told(world, monkeypatch) -> None:  # noqa: F811
    sent: list[str] = []

    async def record(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts, "send", record)
    for missing in ("shop", "shop_count", "email"):
        body = {k: v for k, v in FORM.items() if k != missing}
        assert (await world["anon"].post("/api/invite-requests", json=body)).status_code == 422, missing
    assert (await world["anon"].post("/api/invite-requests", json={**FORM, "shop": "   "})).status_code == 422
    assert (await world["anon"].post("/api/invite-requests", json={**FORM, "shop_count": 0})).status_code == 422
    assert sent == []

    assert (await world["anon"].post("/api/invite-requests", json=FORM)).status_code == 202
    # One line to the alert address: who, which shop, how many shops; not the note.
    assert len(sent) == 1
    assert "maker@example.com" in sent[0] and "etsy.com/shop/makertees" in sent[0] and "Shops: 3" in sent[0]
    assert "new designs weekly" not in sent[0] and "1 pending" in sent[0]
    # A repeat from the same address, and a bot's submission, announce nothing.
    await world["anon"].post("/api/invite-requests", json=FORM)
    await world["anon"].post("/api/invite-requests", json={**FORM, "email": "bot@example.com", "website": "http://spam"})
    assert len(sent) == 1

    # The badge in Admin, and the request with its shop count.
    assert (await world["a"].get("/api/admin/invite-requests/pending")).json() == {"pending": 1}
    assert (await world["b"].get("/api/admin/invite-requests/pending")).status_code == 404
    [row] = (await world["a"].get("/api/admin/invite-requests")).json()
    assert (row["email"], row["shop"], row["shop_count"]) == ("maker@example.com", "etsy.com/shop/makertees", 3)

    # Approving gives the link and a ready message to send: the app sends no e-mail itself.
    approved = (await world["a"].post(f"/api/admin/invite-requests/{row['id']}/approve")).json()
    base = get_settings().frontend_url.rstrip("/")
    assert approved["link"].startswith(f"{base}/register?invite=") and "email=maker%40example.com" in approved["link"]
    assert approved["code"] in approved["link"] and approved["link"] in approved["message"]
    assert "early access" in approved["message"] and "invite-only" in approved["message"] and "beta" not in approved["message"].lower()
    assert (await world["a"].get("/api/admin/invite-requests/pending")).json() == {"pending": 0}
    # The link's code registers that address, with the tick.
    made = await world["anon"].post("/api/account/register", json={
        "email": "maker@example.com", "password": PASSWORD, "invite_code": approved["code"], "accept_terms": True})
    assert made.status_code == 201, made.text


async def test_a_request_nobody_decided_is_deleted_after_ninety_days(world) -> None:  # noqa: F811
    assert (InviteRequest.PENDING_RETENTION_DAYS, InviteRequest.DECIDED_RETENTION_DAYS) == (90, 90)
    async with world["sm"]() as s:
        s.add(InviteRequest(email="old@example.com", shop="Old", shop_count=1, created_at=NOW - timedelta(days=91)))
        s.add(InviteRequest(email="new@example.com", shop="New", shop_count=1, created_at=NOW - timedelta(days=89)))
        await s.commit()
        await purge_expired_rows(s)
        assert [r.email for r in (await s.execute(select(InviteRequest))).scalars()] == ["new@example.com"]


# --- scopes, and the Creativity Standards warning -----------------------------------------------------------------


def test_every_scope_asked_for_has_a_reason_and_none_is_asked_for_without_one() -> None:
    from app.core.config import Settings

    asked = set(Settings(_env_file=None).etsy_scopes.split())
    assert asked == set(oauth.SCOPE_REASONS) == {"listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"}
    assert asked <= oauth.KNOWN_SCOPES and all(len(reason) > 40 for reason in oauth.SCOPE_REASONS.values())


def test_a_print_on_demand_profile_with_no_production_partner_is_warned_about() -> None:
    def profile(template: str, **payload) -> ListingProfile:
        return ListingProfile(name="Standard Tee", content_template=template, cached_payload={"payload_version": 2, **payload})

    note = creativity.production_note(profile("apparel", who_made="i_did", production_partner_ids=[]))
    assert note and "Standard Tee" in note and "Creativity Standards" in note and "production partner" in note
    # Made by someone else, in any template: the listing must name the partner.
    assert "another company or person" in creativity.production_note(profile("digital_products", who_made="someone_else"))
    # A partner is named, the seller makes a non-POD item themselves, or the reference is not read yet: nothing to say.
    assert creativity.production_note(profile("apparel", who_made="i_did", production_partner_ids=[77])) is None
    assert creativity.production_note(profile("digital_products", who_made="i_did")) is None
    assert creativity.production_note(ListingProfile(name="x", content_template="apparel", cached_payload=None)) is None


async def test_the_review_card_carries_the_warning(world) -> None:  # noqa: F811
    bob = world["bob"]
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, bob.profile_id)
        profile.content_template = "apparel"
        profile.cached_payload = {**(profile.cached_payload or {}), "payload_version": 2, "who_made": "i_did", "production_partner_ids": []}
        content = await s.get(GeneratedContent, bob.content_id)
        content.listing_profile_id = profile.id
        await s.commit()
    [card] = (await world["b"].get(f"/api/batches/{bob.batch_id}/content")).json()
    assert card["production_note"] and "production partner" in card["production_note"]
    # A6: said once on the profile (the review page shows one line per profile, linking to it).
    mine = next(p for p in (await world["b"].get("/api/profiles")).json() if p["id"] == str(bob.profile_id))
    assert mine["production_note"] == card["production_note"]
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, bob.profile_id)
        profile.cached_payload = {**profile.cached_payload, "production_partner_ids": [5]}
        await s.commit()
    assert (await world["b"].get(f"/api/batches/{bob.batch_id}/content")).json()[0]["production_note"] is None


async def test_how_it_is_produced_and_the_tools_are_said_never_guessed(world) -> None:  # noqa: F811
    """Etsy's listing editor asks "How does your shop produce this item?" and "What tools are
    used to make this item?". Etsy's OpenAPI spec (checked 2026-10-22) has neither on
    getListing, createDraftListing or updateListing: nothing is read, sent or guessed; each
    draft lists both to tick in Etsy and the profile card says the profile's answers in one line
    (pipeline/how_made.py)."""
    from app.etsy import manual_fields

    keys = [f.key for f in manual_fields.manual_fields_for("apparel")]
    assert keys == ["creativity_production", "creativity_tools"]
    bob = world["bob"]
    mine = next(p for p in (await world["b"].get("/api/profiles")).json() if p["id"] == str(bob.profile_id))
    assert mine["how_made_line"] == "How it's made: not set" and mine["how_made_set"] is False
