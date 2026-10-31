"""The shortcuts the load test added give the same answers as the long way, and go stale nowhere.

Each saves repeated reads on a page of 150 listings (docs/load-test.md). A shortcut that answers
wrongly is worse than a slow page, so each is held to the long way's answer and to forgetting when
what it read changes; none crosses from one account to another.
"""

from __future__ import annotations

import gc
import uuid
from typing import Any

from sqlalchemy import event, select

from app.compliance import check
from app.compliance.trademarks import MEMO_SIZE, compile_blocklist
from app.core import allowance
from app.db.models import (
    AllowanceUse,
    ComplianceFinding,
    ComplianceSeverity,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ProfileShopLink,
    Tenant,
)
from app.db.session import keep_loaded
from app.pipeline import profile_shops, unfinished
from tests.test_draft_records import _batch
from tests.test_load_fixes import T0
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _queries(session: Any) -> list[Any]:
    seen: list[Any] = []

    @event.listens_for(session.sync_session, "do_orm_execute")
    def _one(state: Any) -> None:
        seen.append(state.statement)

    return seen


# -- a request's session -------------------------------------------------------------------------


async def test_a_session_that_keeps_what_it_loaded_asks_once(world) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        keep_loaded(s)
        queries = _queries(s)
        await s.get(Tenant, world["alice"])
        gc.collect()
        await s.get(Tenant, world["alice"])
    assert len(queries) == 1


async def test_without_it_a_dropped_object_is_asked_for_again(world) -> None:  # noqa: ANN001, F811
    """Why ``keep_loaded`` exists: the session holds what it loaded only weakly."""
    async with world["sm"]() as s:
        queries = _queries(s)
        await s.get(Tenant, world["alice"])
        gc.collect()
        await s.get(Tenant, world["alice"])
    assert len(queries) == 2


# -- a profile's link to a shop ------------------------------------------------------------------


async def _profile_of(s: Any, tenant: uuid.UUID) -> ListingProfile:
    return (await s.execute(select(ListingProfile).where(ListingProfile.tenant_id == tenant).limit(1))).scalars().one()


async def test_a_link_is_read_once_per_session_and_forgotten_when_written(world) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        profile = await _profile_of(s, world["alice"])
        other = world["a2"] if profile.connection_id == world["a1"] else world["a1"]
        queries = _queries(s)
        assert await profile_shops.link_for(s, profile, other) is None
        assert await profile_shops.link_for(s, profile, other) is None
        assert len(queries) == 1, "the same answer, without asking again"

        s.add(ProfileShopLink(profile_id=profile.id, connection_id=other, tenant_id=world["alice"]))
        await s.flush()
        assert await profile_shops.link_for(s, profile, other) is not None, "a written link is seen at once"


async def test_a_rolled_back_link_is_not_remembered(world) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        profile = await _profile_of(s, world["alice"])
        other = world["a2"] if profile.connection_id == world["a1"] else world["a1"]
        s.add(ProfileShopLink(profile_id=profile.id, connection_id=other, tenant_id=world["alice"]))
        await s.flush()
        assert await profile_shops.link_for(s, profile, other) is not None
        profile_id = profile.id
        await s.rollback()
        profile = await s.get(ListingProfile, profile_id)
        assert await profile_shops.link_for(s, profile, other) is None


# -- free rewrites -------------------------------------------------------------------------------


async def test_many_designs_at_once_is_the_same_as_one_by_one(world) -> None:  # noqa: ANN001, F811
    designs = ["b:G1", "b:G2", "b:G3", "b:G4"]
    async with world["sm"]() as s:
        for design, used in (("b:G1", 1), ("b:G2", 3), ("b:G3", 5)):
            for _ in range(used):
                allowance.record(s, world["alice"], allowance.REWRITE, design=design)
        s.add(AllowanceUse(tenant_id=world["bob"], kind=allowance.REWRITE, design="b:G4"))  # another account's
        for payload in ({"design": "b:G1", "counts": False}, {"design": "b:G1", "counts": True}, {"design": "b:G4", "counts": False}):
            s.add(Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.replace_images, status=JobStatus.queued, payload=payload))
        await s.commit()
        tenant = await s.get(Tenant, world["alice"])
        one_by_one = {d: await allowance.free_rewrites_left(s, tenant, d) for d in designs}
        current = await allowance.status(s, tenant)
        queries = _queries(s)
        at_once = await allowance.free_rewrites_left_many(s, tenant, designs, current)
    assert at_once == one_by_one == {"b:G1": 1, "b:G2": 0, "b:G3": 0, "b:G4": 2}
    assert len(queries) == 2, "two queries however many designs"
    assert await _empty(world) == {}


async def _empty(world: dict) -> dict:  # noqa: F811
    async with world["sm"]() as s:
        return await allowance.free_rewrites_left_many(s, await s.get(Tenant, world["alice"]), [])


# -- compliance findings -------------------------------------------------------------------------


async def _content(s: Any, tenant: uuid.UUID) -> GeneratedContent:
    return (await s.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == tenant).limit(1))).scalars().one()


def _finding(content: GeneratedContent, severity: ComplianceSeverity, detail: str) -> ComplianceFinding:
    return ComplianceFinding(tenant_id=content.tenant_id, generated_content_id=content.id, severity=severity, rule="test", detail=detail)


async def test_the_page_read_gives_each_listing_the_same_finding_in_one_query(world) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        contents = list((await s.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == world["alice"]))).scalars())
        assert len(contents) >= 2
        s.add(_finding(contents[0], ComplianceSeverity.blocking, "a mark"))
        s.add(_finding(contents[1], ComplianceSeverity.warning, "only a warning"))
        await s.commit()
    async with world["sm"]() as s:
        contents = list((await s.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == world["alice"]))).scalars())
        long_way = [await check.blocking_finding(s, c) for c in contents]
    async with world["sm"]() as s:
        contents = list((await s.execute(select(GeneratedContent).where(GeneratedContent.tenant_id == world["alice"]))).scalars())
        await check.prefetch_blocking(s, contents)
        queries = _queries(s)
        short_way = [await check.blocking_finding(s, c) for c in contents]
    assert short_way == long_way and "a mark" in short_way
    assert queries == [], "answered from the one read"


async def test_scanning_a_listing_again_forgets_what_the_page_read(world) -> None:  # noqa: ANN001, F811
    """Sending and drafting scan first: the trademark list may have grown since the page read."""
    from app.compliance.scanner import rescan

    async with world["sm"]() as s:
        content = await _content(s, world["alice"])
        await check.prefetch_blocking(s, [content])
        assert content.id in s.sync_session.info["blocking"]
        await rescan(s, content)
        assert content.id not in s.sync_session.info["blocking"]


async def test_a_page_only_showing_a_listing_does_not_scan_it_and_sending_does(world, monkeypatch) -> None:  # noqa: ANN001, F811
    scans: list[Any] = []

    async def _scan(session: Any, content: Any) -> list:
        scans.append(content.id)
        return []

    monkeypatch.setattr(check, "rescan", _scan)
    async with world["sm"]() as s:
        content = await _content(s, world["alice"])
        await check.listing_problem(s, content, scan=False)
        assert scans == []
        await check.listing_problem(s, content)
        assert scans == [content.id]


# -- the trademark list --------------------------------------------------------------------------


def test_the_list_finds_the_same_terms_with_its_shortcuts() -> None:
    terms = ["Disney", "Mickey Mouse", "Harry Potter", "Nike"]
    together = compile_blocklist(terms)
    alone = [compile_blocklist([t]) for t in terms]
    for text in ("Retro sunset shirt", "A Disney shirt", "mickey-mouse tee", "MICKEY  MOUSE and nike", "Mickeymouse", "", "Harry Potters"):
        for _ in range(2):  # the second time from memory
            assert sorted(together.find(text)) == sorted(t for one in alone for t in one.find(text)), text


def test_what_it_remembers_cannot_be_changed_by_a_caller() -> None:
    found = compile_blocklist(["Disney"])
    first = found.find("a Disney shirt")
    first.append("something else")
    assert found.find("a Disney shirt") == ["Disney"]


def test_it_remembers_only_so_much() -> None:
    found = compile_blocklist(["Disney"])
    for i in range(MEMO_SIZE + 10):
        found.find(f"text {i}")
    assert len(found._memo) <= MEMO_SIZE
    assert found.find("a Disney shirt") == ["Disney"]


def test_an_empty_list_finds_nothing() -> None:
    assert compile_blocklist([]).find("Disney") == []


# -- unfinished drafts ---------------------------------------------------------------------------


async def _failed(world: dict, content: uuid.UUID, batch: uuid.UUID, tenant: str = "alice", shop: str = "a1") -> None:  # noqa: F811
    async with world["sm"]() as s:
        s.add(Job(tenant_id=world[tenant], connection_id=world[shop], type=JobType.create_draft, status=JobStatus.failed, batch_id=batch,
                  payload={"content_id": str(content)}, created_at=T0, finished_at=T0, last_error="etsy said no"))
        await s.commit()


async def _read(world: dict, tenant: str = "alice") -> list[Any]:  # noqa: F811
    async with world["sm"]() as s:
        return await unfinished.records(s, world[tenant], with_blocks=False)


async def test_the_kept_records_answer_until_something_changes(world, monkeypatch) -> None:  # noqa: ANN001, F811
    unfinished._kept.clear()
    batch, contents = await _batch(world, [("D1", "SKU1"), ("D2", "SKU2")])
    await _failed(world, contents[0], batch)
    first = await _read(world)
    assert {r.content_id for r in first} == {contents[0]}

    reads: list[int] = []
    real = unfinished._records

    async def _counted(*args: Any, **kwargs: Any) -> list[Any]:
        reads.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(unfinished, "_records", _counted)
    assert await _read(world) == first
    assert reads == [], "nothing changed: not read again"

    await _failed(world, contents[1], batch)  # a second draft fails
    assert {r.content_id for r in await _read(world)} == set(contents)
    assert reads == [1], "something changed: read again"


async def test_the_kept_records_are_each_accounts_own(world) -> None:  # noqa: ANN001, F811
    unfinished._kept.clear()
    batch, contents = await _batch(world, [("D1", "SKU1")])
    await _failed(world, contents[0], batch)
    assert len(await _read(world)) == 1
    assert await _read(world, "bob") == []
    assert len(await _read(world)) == 1


async def test_the_shop_setup_is_never_answered_from_the_kept_copy(world, monkeypatch) -> None:  # noqa: ANN001, F811
    """Whether a draft can be finished depends on the shop's setup, which the fingerprint leaves out."""
    unfinished._kept.clear()
    reads: list[int] = []
    real = unfinished._records

    async def _counted(*args: Any, **kwargs: Any) -> list[Any]:
        reads.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(unfinished, "_records", _counted)
    for _ in range(2):
        async with world["sm"]() as s:
            await unfinished.records(s, world["alice"], with_blocks=True)
    assert len(reads) == 2
