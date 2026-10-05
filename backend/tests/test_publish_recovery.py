"""A draft that could not be finished in one go: each way it recovers, on its own.

The fifty-at-once run is tests/test_publish_volume.py; these pin the single
behaviours it relies on, with the publisher's in-memory Etsy.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingProfile,
    ListingPublication,
)
from app.etsy import publisher
from app.etsy.api import RATE_LIMIT_RETRIES, TRANSIENT_RETRIES, EtsyApiClient, RateLimitExceeded
from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError, explain
from app.etsy.publisher import NotYet, PublishBlocked, PublishImage, publication_for, publish_content, publish_live
from app.pipeline.targets import is_fresh, resolve_target
from app.workers import publish as publish_worker
from app.workers import recovery
from app.workers.guards import public_error
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed


class Shop(FakeEtsy):
    """The publisher's fake Etsy, remembering its drafts and their images."""

    def __init__(self) -> None:
        super().__init__()
        self.images: dict[int, list[Any]] = {}
        self.drafts: dict[int, dict[str, Any]] = {}
        self.next_id = 555
        self.gone: set[int] = set()
        self.fail_upload: Exception | None = None  # raised instead of storing
        self.lose_create = False  # the draft is made, the answer is not delivered

    async def create_draft_listing(self, shop_id: int, *, listing: dict[str, Any], **_: Any):
        self.calls.append("create")
        self.last_listing = listing
        listing_id, self.next_id = self.next_id, self.next_id + 1
        self.drafts[listing_id] = {"listing_id": listing_id, "title": listing["title"],
                                   "original_creation_timestamp": int(datetime.now(timezone.utc).timestamp())}
        self.images[listing_id] = []
        if self.lose_create:
            self.lose_create = False
            raise httpx.ReadTimeout("no answer in time")
        return {"listing_id": listing_id}

    async def get_listing(self, listing_id: int, **kw: Any) -> dict[str, Any]:
        if listing_id in self.gone or listing_id not in self.drafts:
            self.calls.append("get_listing")
            raise EtsyClientError(404, body='{"error": "Resource not found"}', path=f"/application/listings/{listing_id}", method="GET")
        return {**await super().get_listing(listing_id, **kw), "state": "draft"}

    async def get_listings_by_shop(self, shop_id: int, **_: Any) -> dict[str, Any]:
        self.calls.append("drafts")
        return {"results": list(self.drafts.values())}

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        self.calls.append("images")
        return {"results": [{"listing_image_id": i} for i in self.images[listing_id]]}

    async def upload_listing_image(self, shop_id, listing_id, *, rank, image_bytes=None, filename=None, listing_image_id=None, **_):  # noqa: ANN001
        self.calls.append("upload")
        if self.fail_upload is not None and len(self.images[listing_id]) == 1:
            raise self.fail_upload
        self.images[listing_id].append(filename if listing_image_id is None else listing_image_id)
        return {}


async def _try(sm: async_sessionmaker, shop: Shop, ids: tuple, *, title: str | None = None):
    _, conn_id, content_id, job_id = ids
    async with sm() as s:
        return await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id),
            connection=await s.get(EtsyConnection, conn_id), sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"), extra_images=[PublishImage(b"b", "back.jpg")],
            fixed_image_ids=[901], client=shop, access_token="tok", config=CONFIG, reference=REFERENCE,
            theme="x", tenant_limit=2000, title=title,
        )


async def _counts(sm: async_sessionmaker) -> tuple[int, int]:
    async with sm() as s:
        return (await s.scalar(select(func.count()).select_from(ListingPublication)),
                await s.scalar(select(func.count()).select_from(DraftAttempt)))


# --- trying again never makes a second draft ---------------------------------------------------


async def test_a_draft_that_failed_partway_is_finished_not_made_again(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    shop = Shop()
    shop.fail_upload = EtsyServerError(503, body="", path="/application/shops/900/listings/555/images", method="POST")
    with pytest.raises(EtsyServerError):
        await _try(async_sm, shop, ids)
    # The draft exists on Etsy with its first image; the app knows which listing it is.
    assert shop.images[555] == ["t.jpg"] and await _counts(async_sm) == (0, 1)
    assert shop.calls.count("upload") == 1 + publisher.UPLOAD_TRIES  # sent again only after checking it had not arrived

    shop.fail_upload = None
    shop.calls.clear()
    result = await _try(async_sm, shop, ids)
    assert result.listing_id == 555 and "create" not in shop.calls and "sections" not in shop.calls
    assert shop.images[555] == ["t.jpg", "back.jpg", 901]  # only what was missing, in order
    assert shop.calls.count("upload") == 2
    assert list(shop.drafts) == [555] and await _counts(async_sm) == (1, 0)


async def test_an_image_stored_without_an_answer_is_not_uploaded_twice(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    shop = Shop()
    real = shop.upload_listing_image

    async def lossy(shop_id, listing_id, **kw):  # noqa: ANN001
        await real(shop_id, listing_id, **kw)
        if kw.get("filename") == "back.jpg" and "lost" not in shop.calls:
            shop.calls.append("lost")
            raise httpx.ReadTimeout("no answer in time")
        return {}

    shop.upload_listing_image = lossy  # type: ignore[method-assign]
    await _try(async_sm, shop, ids)
    assert shop.images[555] == ["t.jpg", "back.jpg", 901]


async def test_a_create_whose_answer_was_lost_is_found_not_repeated(async_sm: async_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(publisher, "ADOPT_WAIT_SECONDS", 0.0)
    ids = await _seed(async_sm)
    shop = Shop()
    shop.lose_create = True
    result = await _try(async_sm, shop, ids)
    assert result.listing_id == 555 and shop.calls.count("create") == 1 and list(shop.drafts) == [555]
    assert await _counts(async_sm) == (1, 0)


async def test_an_unanswered_create_that_made_nothing_waits_before_it_is_sent_again(async_sm: async_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(publisher, "ADOPT_WAIT_SECONDS", 0.0)
    ids = await _seed(async_sm)
    shop = Shop()

    async def down(shop_id: int, *, listing: dict[str, Any], **_: Any):
        shop.calls.append("create")
        raise EtsyServerError(500, body="", path="/application/shops/900/listings", method="POST")

    working = shop.create_draft_listing
    shop.create_draft_listing = down  # type: ignore[method-assign]
    with pytest.raises(NotYet):
        await _try(async_sm, shop, ids)
    shop.create_draft_listing = working  # type: ignore[method-assign]
    # Straight away: Etsy's draft list may not show it yet, so nothing is sent.
    with pytest.raises(NotYet):
        await _try(async_sm, shop, ids)
    assert shop.calls.count("create") == 1 and not shop.drafts
    # Later, with still no such draft in the shop, it is created.
    async with async_sm() as s:
        (await s.scalar(select(DraftAttempt))).create_sent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await s.commit()
    assert (await _try(async_sm, shop, ids)).listing_id == 555 and list(shop.drafts) == [555]


async def test_a_draft_deleted_on_etsy_since_is_started_again(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    shop = Shop()
    shop.fail_upload = EtsyServerError(503)
    with pytest.raises(EtsyServerError):
        await _try(async_sm, shop, ids)
    shop.fail_upload = None
    shop.gone.add(555)  # the seller deleted the half-made draft in Shop Manager
    result = await _try(async_sm, shop, ids)
    assert result.listing_id == 556 and shop.images[556] == ["t.jpg", "back.jpg", 901]


async def test_text_edited_between_tries_reaches_the_draft(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    shop = Shop()
    shop.fail_upload = EtsyServerError(503)
    with pytest.raises(EtsyServerError):
        await _try(async_sm, shop, ids)
    shop.fail_upload = None
    edited = "Edited " + "Retro Graphic Tee, " * 6 + "Gift"
    await _try(async_sm, shop, ids, title=edited)
    assert shop.last_update["title"] == edited and list(shop.drafts) == [555]


# --- the client repeats only what is safe to repeat --------------------------------------------


def _client(responses: list[Any]) -> tuple[EtsyApiClient, list[str]]:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        answer = responses[min(len(sent), len(responses)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, json={"error": "x"} if answer >= 400 else {"listing_id": 1})

    async def no_sleep(_: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EtsyApiClient(client_id="k", shared_secret="s", http_client=http, sleep=no_sleep), sent


async def test_reads_and_idempotent_writes_are_repeated_after_no_answer_or_5xx() -> None:
    client, sent = _client([503, httpx.ReadTimeout("slow"), 200])
    assert await client.get_listing(1, access_token="t") == {"listing_id": 1} and len(sent) == 3
    client, sent = _client([502, 200])
    await client.update_listing_inventory(1, inventory={"products": []}, access_token="t")
    assert sent == ["PUT", "PUT"]
    client, sent = _client([503])
    with pytest.raises(EtsyServerError):
        await client.get_listing(1, access_token="t")
    assert len(sent) == 1 + TRANSIENT_RETRIES  # and then it stops


async def test_a_create_or_an_upload_is_never_resent_blind() -> None:
    client, sent = _client([503, 200])
    with pytest.raises(EtsyServerError):
        await client.create_draft_listing(1, listing={"title": "x"}, access_token="t")
    client2, sent2 = _client([httpx.ReadTimeout("slow"), 200])
    with pytest.raises(httpx.ReadTimeout):
        await client2.upload_listing_image(1, 2, rank=1, image_bytes=b"x", access_token="t")
    assert sent == ["POST"] and sent2 == ["POST"]
    # Going live is checked by reading the listing back, not by sending it again.
    client3, sent3 = _client([httpx.ReadTimeout("slow"), 200])
    with pytest.raises(httpx.ReadTimeout):
        await client3.update_listing(1, 2, updates={"state": "active"}, access_token="t", repeat=False)
    assert sent3 == ["PATCH"]


async def test_a_429_is_still_retried_for_any_method_then_given_up() -> None:
    client, sent = _client([429])
    with pytest.raises(EtsyRateLimited):
        await client.create_draft_listing(1, listing={"title": "x"}, access_token="t")
    assert len(sent) == 1 + RATE_LIMIT_RETRIES


# --- what the seller is told --------------------------------------------------------------------


def test_a_refusal_says_which_step_and_why() -> None:
    refused = EtsyClientError(400, body='{"error": "Invalid value for tags: must be 20 characters or fewer"}',
                              path="/application/shops/9/listings", method="POST")
    assert public_error(refused) == "Etsy refused it while creating the draft: Invalid value for tags: must be 20 characters or fewer"
    gone = EtsyClientError(404, body='{"error": "Resource not found"}', path="/application/shops/9/listings/5", method="PATCH")
    assert "could not find the listing while updating the listing" in public_error(gone)
    assert "reconnect the shop" in public_error(EtsyClientError(403, path="/application/listings/5/inventory", method="PUT"))
    assert "(it answered 400) without saying why" in explain(EtsyClientError(400, body="<html>", path="/x", method="GET"))
    assert "Etsy had a problem of its own while adding an image (it answered 503)" in public_error(
        EtsyServerError(503, path="/application/shops/9/listings/5/images", method="POST"))


def test_no_answer_and_a_blocked_listing_are_named_not_called_unexpected() -> None:
    assert public_error(httpx.ReadTimeout("x")) == "Etsy did not answer in time. Nothing is wrong with the listing: try again."
    assert "connection to Etsy dropped" in public_error(httpx.ConnectError("x"))
    assert public_error(PublishBlocked("the listing is no longer approved")).startswith("Not published: the listing is no longer approved")
    assert "unexpectedly" in public_error(KeyError("internal"))  # anything else stays generic
    # Nothing token-like gets through with Etsy's reason.
    leaky = EtsyClientError(400, body='{"error": "bad token Bearer 12345.abcdefghijklmnopqrstuvwxyz"}', path="/x", method="GET")
    assert "abcdefghijklmnopqrstuvwxyz" not in public_error(leaky)


# --- what a job that could not finish becomes ----------------------------------------------------


class _Queue(list):
    async def __call__(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.append((function, args, kwargs))


async def _job(sm: async_sessionmaker) -> uuid.UUID:
    _, _, _, job_id = await _seed(sm)
    async with sm() as s:
        job = await s.get(Job, job_id)
        job.status, job.started_at = JobStatus.running, datetime.now(timezone.utc)
        await s.commit()
    return job_id


@pytest.mark.parametrize(
    ("exc", "reason", "delay"),
    [
        (EtsyRateLimited(retry_after=5), recovery.WAIT_ETSY_RATE, 60.0),  # never sooner than a minute
        (EtsyRateLimited(retry_after=240), recovery.WAIT_ETSY_RATE, 240.0),
        (httpx.ReadTimeout("slow"), recovery.WAIT_ETSY_DOWN, 30.0),
        (EtsyServerError(503), recovery.WAIT_ETSY_DOWN, 30.0),
        (NotYet("checking"), recovery.WAIT_ETSY_DOWN, 60.0),
    ],
)
async def test_what_will_pass_waits_and_runs_again(async_sm: async_sessionmaker, exc, reason, delay) -> None:
    job_id, queue = await _job(async_sm), _Queue()
    async with async_sm() as s:
        assert await recovery.after_failure({"enqueue": queue}, s, job_id, exc, "run_publish_job") == "deferred"
    async with async_sm() as s:
        job = await s.get(Job, job_id)
    assert (job.status, job.paused_reason, job.attempts, job.last_error) == (JobStatus.queued, reason, 1, None)
    assert queue == [("run_publish_job", (str(job_id),), {"_defer_by": delay})]


async def test_after_its_attempts_it_fails_saying_nothing_is_wrong_with_the_listing(async_sm: async_sessionmaker) -> None:
    job_id, queue = await _job(async_sm), _Queue()
    async with async_sm() as s:
        (await s.get(Job, job_id)).attempts = 4
        await s.commit()
        assert await recovery.after_failure({"enqueue": queue}, s, job_id, EtsyServerError(503), "run_publish_job") == "failed"
    async with async_sm() as s:
        job = await s.get(Job, job_id)
    assert job.status is JobStatus.failed and "carries on where it stopped" in job.last_error and not queue


async def test_a_real_refusal_fails_at_once_with_its_reason(async_sm: async_sessionmaker) -> None:
    job_id, queue = await _job(async_sm), _Queue()
    refused = EtsyClientError(400, body='{"error": "Invalid shipping_profile_id"}', path="/application/shops/9/listings", method="POST")
    async with async_sm() as s:
        assert await recovery.after_failure({"enqueue": queue}, s, job_id, refused, "run_publish_job") == "failed"
    async with async_sm() as s:
        job = await s.get(Job, job_id)
    assert job.status is JobStatus.failed and job.last_error.endswith("Invalid shipping_profile_id") and not queue


async def test_the_days_budget_running_out_partway_waits_for_the_reset(async_sm: async_sessionmaker) -> None:
    job_id, queue = await _job(async_sm), _Queue()
    async with async_sm() as s:
        result = await recovery.after_failure({"enqueue": queue, "quota": None}, s, job_id, RateLimitExceeded("out"), "run_publish_job")
    async with async_sm() as s:
        job = await s.get(Job, job_id)
    assert result == "deferred" and job.status is JobStatus.queued and job.attempts == 0  # not counted against it
    assert queue[0][2]["_defer_by"] > 0 and job.scheduled_at is not None


async def test_a_cancelled_job_is_queued_again_and_a_dead_workers_job_is_found(async_sm: async_sessionmaker, test_settings) -> None:
    job_id, queue = await _job(async_sm), _Queue()
    ctx = {"enqueue": queue, "sessionmaker": async_sm}
    await recovery.interrupted(ctx, None, job_id, "run_publish_job")
    async with async_sm() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.paused_reason) == (JobStatus.queued, recovery.WAIT_INTERRUPTED)
        # A worker that was killed says nothing: the row stays "running".
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc) - timedelta(seconds=test_settings.worker_job_timeout + 600)
        await s.commit()
    assert await recovery.recover_interrupted_jobs(ctx) == 1
    async with async_sm() as s:
        assert (await s.get(Job, job_id)).status is JobStatus.queued
    assert [q[0] for q in queue] == ["run_publish_job", "run_publish_job"]
    assert await recovery.recover_interrupted_jobs(ctx) == 0  # one still inside its time is left alone


async def test_a_job_never_runs_twice_at_once() -> None:
    ctx = {"redis": FakeAsyncRedis()}
    async with recovery.holding(ctx, "j1") as first:
        async with recovery.holding(ctx, "j1") as second:
            assert first is True and second is False
        async with recovery.holding(ctx, "j2") as other:
            assert other is True
    async with recovery.holding(ctx, "j1") as again:  # released when the run ends
        assert again is True


# --- a stale profile is refreshed by the job, once ----------------------------------------------


async def test_a_profile_read_by_an_older_version_is_stale_and_the_job_refreshes_it_once(async_sm: async_sessionmaker, monkeypatch) -> None:
    _, conn_id, content_id, _ = await _seed(async_sm)
    old = {k: v for k, v in REFERENCE.items() if k != "payload_version"}
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        profile = ListingProfile(tenant_id=content.tenant_id, connection_id=conn_id, name="Tee", reference_listing_id=1,
                                 content_template="apparel", confirmed=True, cached_payload={**old, "description": "R\n\nB"},
                                 updated_at=datetime.now(timezone.utc))
        s.add(profile)
        await s.flush()
        content.listing_profile_id = profile.id
        await s.commit()
        profile_id = profile.id
        assert not is_fresh(profile)
        # Said up front, for the whole batch, instead of failing each listing in the worker.
        target = await resolve_target(s, content, await s.get(EtsyConnection, conn_id))
        assert not target.ok and target.stale and "older version" in target.reason

    refreshes: list[str] = []

    async def refresh(ctx: dict[str, Any], pid: str) -> str:
        refreshes.append(pid)
        await asyncio.sleep(0.01)
        async with async_sm() as s:
            row = await s.get(ListingProfile, uuid.UUID(pid))
            row.cached_payload = {**REFERENCE, "description": "R\n\nB"}
            row.updated_at = datetime.now(timezone.utc)
            await s.commit()
        return "refreshed"

    import app.workers.profiles as profiles_worker

    monkeypatch.setattr(profiles_worker, "refresh_profile", refresh)

    async def one() -> bool:
        async with async_sm() as s:
            target = await publish_worker._fresh_target(
                {}, s, await s.get(GeneratedContent, content_id), await s.get(EtsyConnection, conn_id), profile_id)
            return target.ok

    assert await asyncio.gather(*(one() for _ in range(5))) == [True] * 5
    assert refreshes == [str(profile_id)]  # five drafts, one refresh


# --- going live on a draft that is gone ----------------------------------------------------------


async def test_publishing_a_draft_deleted_in_shop_manager_says_so_and_lets_it_be_made_again(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm)

    class Gone(FakeEtsy):
        async def update_listing(self, shop_id, listing_id, **_):  # noqa: ANN001
            raise EtsyClientError(404, body='{"error": "Resource not found"}', path=f"/application/shops/900/listings/{listing_id}", method="PATCH")

        async def get_listing(self, listing_id, **_):  # noqa: ANN001
            raise EtsyClientError(404, body='{"error": "Resource not found"}', path=f"/application/listings/{listing_id}", method="GET")

    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        s.add(ListingPublication(tenant_id=content.tenant_id, content_id=content_id, connection_id=conn_id,
                                 etsy_listing_id=555, state="draft"))
        await s.commit()
    async with async_sm() as s:
        with pytest.raises(ValueError, match="no longer on Etsy"):
            await publish_live(s, job_id=job_id, content=await s.get(GeneratedContent, content_id),
                               connection=await s.get(EtsyConnection, conn_id), client=Gone(), access_token="tok", tenant_limit=2000)
    async with async_sm() as s:
        assert await publication_for(s, content_id, conn_id) is None  # "Create draft" is offered again
        # The record is marked, never deleted: counts and Analytics keep it (STEP 0).
        [kept] = (await s.execute(select(ListingPublication))).scalars().all()
        assert kept.state == "deleted_on_etsy" and kept.deleted_on_etsy_at is not None
        assert kept.etsy_listing_id == 555 and kept.connection_id == conn_id
