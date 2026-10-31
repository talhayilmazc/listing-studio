"""What the load test found (scripts/load/, docs/load-test.md), held in place.

* the term check finds exactly what the term-by-term check found, in far fewer searches;
* "unfinished drafts" reads only the designs with something to say, not every draft ever made,
  and still finds them all;
* a batch's state does not read another account's queued jobs;
* a go-live Etsy answered 5xx to, and that is still a draft, waits and goes again;
* writing a listing does not hold a database connection while the model answers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select

from app.core.config import get_settings
from app.db.models import DraftAttempt, EtsyConnection, GeneratedContent, Job, JobStatus, JobType
from app.pipeline import content as content_module
from app.pipeline import group_state, unfinished
from app.pipeline.content import GeneratedListing, _hits, _policy_errors, policy_for
from tests.test_draft_records import _batch
from tests.support import fake_response
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_regenerate import VISION, _content_reply, _contents, _generate, _group, llm  # noqa: F401  (fixture)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


# -- the term check ------------------------------------------------------------------------------

POLICY = policy_for("apparel")
EVERY_TERM = tuple({*POLICY.forbidden_terms, *POLICY.forbidden_tag_terms, *POLICY.forbidden_filler_terms, *POLICY.required_type_terms})


@pytest.mark.parametrize("text", [
    "Retro Sunset Graphic Shirt",
    "shirt",
    "T-Shirt for hikers",
    "opening a png",          # 'png' as a word, not inside 'opening'
    "openingpng",             # inside a word: no term
    "SVG DIGITAL DOWNLOAD",
    "",
    "teen hoodies",           # 'tee' and 'hoodie' are inside longer words here: no match
])
def test_hits_finds_exactly_what_each_term_alone_finds(text: str) -> None:
    alone = {term for term in EVERY_TERM if content_module._has_term(text, term)}
    assert _hits(text, EVERY_TERM) == alone


def test_terms_are_whole_words_and_ignore_case() -> None:
    assert _hits("Vintage SHIRT", ("shirt", "tee")) == {"shirt"}
    assert _hits("shirts", ("shirt",)) == set()
    assert _hits("teen", ("tee",)) == set()
    assert _hits("anything", ()) == set()


def test_a_listing_with_a_product_type_passes_and_one_without_does_not() -> None:
    good = GeneratedListing(title="Retro Sunset Graphic Shirt", tags=["retro sunset shirt", "hiking"], description="d")
    assert not [e for e in _policy_errors(good, POLICY) if "product type" in e]
    bad = GeneratedListing(title="Retro Sunset Graphic", tags=["retro sunset", "hiking"], description="d")
    errors = _policy_errors(bad, POLICY)
    assert any(e.startswith("title must name the product type") for e in errors)
    assert any(e.startswith("at least one tag must name the product type") for e in errors)


def test_every_forbidden_term_is_still_named_in_title_and_tags() -> None:
    term = POLICY.forbidden_terms[0]
    listing = GeneratedListing(title=f"Shirt {term}", tags=[f"shirt {term}", "plain"], description="d")
    errors = _policy_errors(listing, POLICY)
    assert any(f"remove '{term}' from the title" in e for e in errors)
    assert any(f"remove '{term}' from these tags" in e and f"shirt {term}" in e for e in errors)


def test_a_clean_listing_costs_far_fewer_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of it: a page of 150 listings ran ~60,000 term searches."""
    calls = 0
    real = content_module._has_term

    def counting(text: str, term: str) -> bool:
        nonlocal calls
        calls += 1
        return real(text, term)

    monkeypatch.setattr(content_module, "_has_term", counting)
    listing = GeneratedListing(title="Retro Sunset Graphic Shirt", tags=[f"plain word{i}" for i in range(13)], description="d")
    _policy_errors(listing, POLICY)
    # Only the terms of the lists that matched at all are looked at one by one.
    assert calls < len(POLICY.required_type_terms) * 2


# -- unfinished drafts ---------------------------------------------------------------------------


def _count_loaded(session: Any, kind: type) -> list[int]:
    loaded = [0]

    @event.listens_for(session.sync_session, "loaded_as_persistent")
    def _seen(_session: Any, instance: Any) -> None:
        if isinstance(instance, kind):
            loaded[0] += 1

    return loaded


async def _draft_jobs(world: Any, contents: list[uuid.UUID], batch: uuid.UUID, status: JobStatus, shop: str = "a1") -> None:  # noqa: F811
    async with world["sm"]() as s:
        for c in contents:
            s.add(Job(tenant_id=world["alice"], connection_id=world[shop], type=JobType.create_draft, status=status, batch_id=batch,
                      payload={"content_id": str(c)}, created_at=T0, finished_at=T0, last_error="etsy said no" if status is JobStatus.failed else None))
        await s.commit()


async def test_unfinished_reads_only_the_designs_with_something_to_say(world) -> None:  # noqa: ANN001, F811
    batch, contents = await _batch(world, [(f"D{i:02d}", f"SKU{i:02d}") for i in range(30)])
    await _draft_jobs(world, contents[:28], batch, JobStatus.succeeded)  # most drafts just worked
    await _draft_jobs(world, contents[28:29], batch, JobStatus.failed)   # one failed
    await _draft_jobs(world, contents[28:29], batch, JobStatus.succeeded)  # (and was redone)
    async with world["sm"]() as s:
        # One made a listing and stopped there.
        s.add(DraftAttempt(tenant_id=world["alice"], connection_id=world["a1"], content_id=contents[29], etsy_listing_id=7777,
                           title="half made", create_sent_at=T0, created_at=T0))
        await s.commit()
    async with world["sm"]() as s:
        loaded = _count_loaded(s, Job)
        records = await unfinished._records(s, world["alice"], with_blocks=False)
    assert {r.content_id for r in records} == {contents[28], contents[29]}
    assert next(r for r in records if r.content_id == contents[29]).etsy_listing_id == 7777
    failed = next(r for r in records if r.content_id == contents[28])
    assert failed.last_error == "etsy said no"
    assert loaded[0] == 2, "only the failed design's jobs are read, not every draft ever made"


async def test_a_failed_job_keeps_its_other_jobs_and_finishing_state(world) -> None:  # noqa: ANN001, F811
    batch, contents = await _batch(world, [("D1", "SKU1")])
    await _draft_jobs(world, contents, batch, JobStatus.failed)
    await _draft_jobs(world, contents, batch, JobStatus.queued)  # "Finish draft" pressed: it is on its way
    async with world["sm"]() as s:
        records = await unfinished._records(s, world["alice"], with_blocks=False)
    assert len(records) == 1 and records[0].finishing is True


async def test_an_account_with_nothing_unfinished_reads_almost_nothing(world) -> None:  # noqa: ANN001, F811
    batch, contents = await _batch(world, [(f"D{i}", f"SKU{i}") for i in range(5)])
    await _draft_jobs(world, contents, batch, JobStatus.succeeded)
    async with world["sm"]() as s:
        loaded = _count_loaded(s, GeneratedContent)
        jobs = _count_loaded(s, Job)
        assert await unfinished._records(s, world["alice"], with_blocks=False) == []
    assert loaded[0] == 0 and jobs[0] == 0


async def test_only_this_accounts_records(world) -> None:  # noqa: ANN001, F811
    batch, contents = await _batch(world, [("D1", "SKU1")])
    await _draft_jobs(world, contents, batch, JobStatus.failed)
    async with world["sm"]() as s:
        assert await unfinished._records(s, world["bob"], with_blocks=False) == []


# -- a batch's state -----------------------------------------------------------------------------


async def test_a_batch_is_busy_for_its_own_jobs_only(world) -> None:  # noqa: ANN001, F811
    mine, _ = await _batch(world, [("D1", "SKU1")])
    other, _ = await _batch(world, [("D2", "SKU2")])
    async with world["sm"]() as s:
        # Another batch's queue (one seller's 3,000 queued drafts): this batch is not busy for it.
        s.add(Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.create_draft, status=JobStatus.queued, batch_id=other, payload={}))
        await s.commit()
    async with world["sm"]() as s:
        states = await group_state.group_states(s, batch_ids={mine}, with_removed=True)
    assert states[(mine, "D1")].state != group_state.PENDING
    async with world["sm"]() as s:
        # Named in the job's payload only: still its own.
        s.add(Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.replace_images, status=JobStatus.running,
                  payload={"batch_id": str(mine)}))
        await s.commit()
    async with world["sm"]() as s:
        states = await group_state.group_states(s, batch_ids={mine}, with_removed=True)
    assert states[(mine, "D1")].state == group_state.PENDING


async def test_the_whole_app_view_still_sees_every_busy_batch(world) -> None:  # noqa: ANN001, F811
    """Upload retention asks about every batch: a queued job anywhere still keeps its files."""
    mine, _ = await _batch(world, [("D1", "SKU1")])
    async with world["sm"]() as s:
        s.add(Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.create_draft, status=JobStatus.queued, batch_id=mine, payload={}))
        await s.commit()
    async with world["sm"]() as s:
        states = await group_state.group_states(s, with_removed=True)
    assert states[(mine, "D1")].state == group_state.PENDING


# -- a go-live Etsy answered 5xx to --------------------------------------------------------------


class _Etsy:
    """Answers the state change with ``error`` once; the listing stays a draft until it works."""

    def __init__(self, error: Exception) -> None:
        self.error: Exception | None = error
        self.state = "draft"
        self.changes = 0

    async def get_listing(self, listing_id: int, **_: Any) -> dict:
        return {"listing_id": listing_id, "state": self.state, "state_timestamp": 1}

    async def update_listing(self, shop_id: int, listing_id: int, *, updates: dict, repeat: bool | None = None, **_: Any) -> dict:
        self.changes += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        self.state = updates["state"]
        return {"listing_id": listing_id, "state": self.state}


@pytest.mark.parametrize("make_error", [
    lambda: httpx.ReadTimeout("no answer"),
    lambda: __import__("app.etsy.errors", fromlist=["EtsyServerError"]).EtsyServerError(status_code=503, body={}, path="/x", method="PATCH"),
])
async def test_a_go_live_etsy_did_not_take_goes_again_and_never_counts_as_live(world, make_error) -> None:  # noqa: ANN001, F811
    from app.db.models import EtsyConnection, ListingPublication
    from app.etsy import publisher
    from app.workers import recovery

    batch, contents = await _batch(world, [("D1", "SKU1")])
    async with world["sm"]() as s:
        publication = ListingPublication(tenant_id=world["alice"], content_id=contents[0], connection_id=world["a1"],
                                         etsy_listing_id=4242, state="draft", title="t", sku="SKU1")
        job = Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.publish_live, status=JobStatus.running,
                  batch_id=batch, payload={"content_id": str(contents[0])}, max_attempts=5)
        s.add_all([publication, job])
        await s.commit()
        job_id = job.id
    etsy = _Etsy(make_error())

    async def go() -> None:
        async with world["sm"]() as s:
            content = await s.get(GeneratedContent, contents[0])
            shop = await s.get(EtsyConnection, world["a1"])
            await publisher.publish_live(s, job_id=job_id, content=content, connection=shop, client=etsy, access_token="t",
                                         tenant_limit=1000, confirm_attempts=1, confirm_wait=0)

    with pytest.raises((httpx.TransportError, publisher.EtsyServerError)) as caught:
        await go()
    queued: list[tuple] = []

    async def enqueue(*args: Any, **kwargs: Any) -> None:
        queued.append((args, kwargs))

    async with world["sm"]() as s:
        outcome = await recovery.after_failure({"enqueue": enqueue}, s, job_id, caught.value, "run_publish_live_job")
    assert outcome == "deferred" and queued and queued[0][0][0] == "run_publish_live_job"
    async with world["sm"]() as s:
        job = await s.get(Job, job_id)
        assert job.status is JobStatus.queued and job.paused_reason == recovery.WAIT_ETSY_DOWN
        stored = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == contents[0]))).scalar_one()
        assert stored.state == "draft", "never recorded as live while Etsy says draft"
    await go()  # the job runs again: this time Etsy takes it
    async with world["sm"]() as s:
        stored = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == contents[0]))).scalar_one()
        assert stored.state == "active" and etsy.changes == 2




# -- no connection held while the model answers --------------------------------------------------


@pytest.fixture()
def open_transactions():
    """The sessions holding a database transaction right now."""
    from sqlalchemy.orm import Session

    holding: set[int] = set()

    def began(session: Any, transaction: Any, connection: Any) -> None:
        holding.add(id(session))

    def ended(session: Any, transaction: Any) -> None:
        if transaction.parent is None:
            holding.discard(id(session))

    event.listen(Session, "after_begin", began)
    event.listen(Session, "after_transaction_end", ended)
    yield holding
    event.remove(Session, "after_begin", began)
    event.remove(Session, "after_transaction_end", ended)


async def test_writing_a_listing_holds_no_connection_while_the_model_answers(ctx, llm, open_transactions) -> None:  # noqa: ANN001, F811
    batch, _, profile = await _group(ctx)
    seen: list[int] = []
    real_create = llm.create

    async def create(**kwargs: Any) -> Any:
        seen.append(len(open_transactions))
        return await real_create(**kwargs)

    llm.create = create  # type: ignore[method-assign]
    llm._responses += [fake_response(VISION), fake_response(_content_reply())]
    res = await _generate(ctx, batch, profile, replace=True)
    assert res["generated"] == 1, res
    assert seen == [0, 0], "a transaction was open during a model call: it holds a pooled connection for tens of seconds"
    assert len(await _contents(ctx, batch)) == 1  # and the listing was still saved


async def test_a_failed_model_call_still_records_the_failure_and_answers(ctx, llm) -> None:  # noqa: ANN001, F811
    batch, _, profile = await _group(ctx)

    async def create(**kwargs: Any) -> Any:
        raise RuntimeError("the provider's words")

    llm.create = create  # type: ignore[method-assign]
    res = await _generate(ctx, batch, profile, replace=True)
    assert res["failed"] == 1 and "provider" not in str(res)


async def test_a_rewrite_for_a_shop_holds_no_connection_while_the_model_answers(open_transactions) -> None:
    """The commit comes first in ``_write``, before the model's client is even built."""
    import inspect

    from app.api import another_shop

    source = inspect.getsource(another_shop._write)
    assert source.index("await session.commit()") < source.index("await analyzer.analyze(")


async def test_shortening_holds_no_connection_while_the_model_answers() -> None:
    import inspect

    from app.api import personalization_fix

    source = inspect.getsource(personalization_fix.shorten)
    assert source.index("await session.commit()") < source.index("await F.shorten(")


# -- the worker gives way to the API -------------------------------------------------------------


def test_the_worker_lowers_its_own_cpu_priority_and_never_fails_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import settings as worker_settings

    asked: list[int] = []
    monkeypatch.setattr(worker_settings.os, "nice", asked.append, raising=False)
    worker_settings._give_way_to_the_api(get_settings().model_copy(update={"worker_nice": 7}))
    assert asked == [7]
    # The default keeps the worker's own priority: only its image threads give way.
    assert get_settings().worker_nice == 0
    worker_settings._give_way_to_the_api(get_settings())
    assert asked == [7]

    def refused(_: int) -> None:
        raise OSError("not allowed")

    monkeypatch.setattr(worker_settings.os, "nice", refused, raising=False)
    worker_settings._give_way_to_the_api(get_settings().model_copy(update={"worker_nice": 7}))  # only a priority: it still starts


# -- remembered keywords stay correct ------------------------------------------------------------


def test_keywords_are_a_fresh_list_each_time() -> None:
    from app.pipeline import search_rules

    first = search_rules.keywords("Nurses love cats")
    first.append("changed")
    assert "changed" not in search_rules.keywords("Nurses love cats")
    assert search_rules.keywords("Nurses love cats") == [search_rules.stem(w) for w in search_rules.words("Nurses love cats") if w not in search_rules._STOP]


# -- the batch list's light read -----------------------------------------------------------------


async def test_the_light_read_names_the_same_shops_as_the_full_one(world) -> None:  # noqa: ANN001, F811
    from app.db.models import ListingPublication, UploadBatch

    batch, contents = await _batch(world, [("D1", "SKU1"), ("D2", "SKU2")])
    async with world["sm"]() as s:
        s.add(ListingPublication(tenant_id=world["alice"], content_id=contents[0], connection_id=world["a2"], etsy_listing_id=99,
                                 state="draft", title="t", sku="SKU1"))
        (await s.get(UploadBatch, batch)).send_to = {"group_ids": [], "connection_ids": [str(world["a1"])]}
        await s.commit()
    async with world["sm"]() as s:
        full = await group_state.group_states(s, batch_ids={batch}, with_removed=True)
    async with world["sm"]() as s:
        light = await group_state.group_states(s, batch_ids={batch}, with_removed=True, targets_only=True)
    assert set(full) == set(light)
    # The light read leaves out only the shops drafted in; the batch list reads those itself.
    assert light[(batch, "D1")].targets == {world["a1"]}
    assert full[(batch, "D1")].targets == {world["a1"], world["a2"]}
    assert light[(batch, "D2")].targets == full[(batch, "D2")].targets


async def test_the_batch_list_names_every_shop_a_batch_goes_to(world) -> None:  # noqa: ANN001, F811
    from app.api.batches import _summaries
    from app.db.models import ListingPublication, UploadBatch

    batch, contents = await _batch(world, [("D1", "SKU1")])
    async with world["sm"]() as s:
        s.add(ListingPublication(tenant_id=world["alice"], content_id=contents[0], connection_id=world["a2"], etsy_listing_id=99,
                                 state="draft", title="t", sku="SKU1"))
        (await s.get(UploadBatch, batch)).send_to = {"group_ids": [], "connection_ids": [str(world["a1"])]}
        await s.commit()
    async with world["sm"]() as s:
        one, two = await s.get(EtsyConnection, world["a1"]), await s.get(EtsyConnection, world["a2"])
        names = {one.shop_name, two.shop_name}
        summary = (await _summaries(s, world["alice"], [await s.get(UploadBatch, batch)]))[batch]
    assert set(summary.shop_names) == names


# -- heavy image work takes turns ----------------------------------------------------------------


async def test_heavy_image_work_takes_turns_and_gives_its_answer() -> None:
    import asyncio
    import threading
    import time

    from app.core import image_work

    running = 0
    most = 0
    lock = threading.Lock()

    def work(n: int) -> int:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        return n * 2

    assert await asyncio.gather(*(image_work.run(work, n) for n in range(9))) == [n * 2 for n in range(9)]
    assert most <= get_settings().image_work_threads


async def test_a_failure_in_image_work_is_the_callers_and_frees_its_turn() -> None:
    from app.core import image_work

    def broken() -> None:
        raise ValueError("not an image")

    for _ in range(6):
        with pytest.raises(ValueError):
            await image_work.run(broken)


async def test_an_upload_holds_no_connection_while_its_image_is_processed(ctx, open_transactions, monkeypatch, tmp_path) -> None:  # noqa: ANN001, F811
    import io

    from PIL import Image

    from app.api import deps
    from app.core import image_work
    from app.pipeline.storage import LocalStorage

    seen: list[int] = []
    real = image_work.run

    async def watching(function: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(len(open_transactions))
        return await real(function, *args, **kwargs)

    monkeypatch.setattr(image_work, "run", watching)
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: LocalStorage(tmp_path)
    created = await ctx["client"].post("/api/batches", json={})
    assert created.status_code == 201, created.text
    buf = io.BytesIO()
    Image.new("RGB", (900, 900), "orange").save(buf, "JPEG")
    res = await ctx["client"].post(f"/api/batches/{created.json()['id']}/assets", files={"file": ("A1.jpg", buf.getvalue(), "image/jpeg")})
    assert res.status_code == 201, res.text
    assert seen == [0], "a transaction was open while the image was processed: it holds a pooled connection"
