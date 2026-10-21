"""Half-made and failed drafts: one record per design and shop, with what to do about it.

A draft is some fifteen Etsy requests. ``createDraftListing`` comes first; when a later
step is refused (the inventory, a property, the personalization question, an image),
the listing stays on Etsy as a draft with only part of its settings. Finishing it runs
the same draft job again: it reads that listing back and carries on with it, so no
second listing is made, and nothing is ever deleted on Etsy.

**Where a draft's Etsy listing id is kept** (each is read; the first that has it wins):

1. the :class:`DraftAttempt` (written before the create request, holds the id as soon
   as Etsy returns it). It goes when the draft finishes, with its listing text (deleting
   a batch deletes it) and after 30 days;
2. the job's **create snapshot** (:class:`ListingSnapshot`, ``operation: create_draft``,
   written the moment Etsy returned the id; kept 90 days, survives the batch);
3. the path of the refused request in Etsy's answer kept with the failed job
   (``job.payload["etsy_error"]``, ``/listings/<id>/inventory``; since 2026-10-18).

When none has it the record says so plainly ("never stored"). A draft whose batch was
deleted is still found (2, 3): its design is then known by its batch and title only.

**Records are merged per design and shop** (pipeline/design_match.py): the failed jobs of
one listing are one record; a newer half-made draft of the same design makes the older
one a duplicate to delete on Etsy; a failure of the same design with no listing of its
own belongs to the record that has one.

**Replaced**: a draft that later succeeded in the same shop for the same design (the same
listing, the same batch and group, the SKU, the group's name across batches, or the
title; the first kind both have decides) replaces a half-made one, which is then to be
deleted on Etsy by the seller; the matching kind is kept (``replaced_key``).

**Categories** (``category``; the seller's list and the operator's summary use the same):
finish now, fix the personalization question then finish, set up the shop then finish,
replaced (delete on Etsy), batch deleted (the text is gone: delete it on Etsy or finish
it in Shop Manager), nothing to do (finished or redone since), and, for records without
a listing id, not on Etsy or not looked up yet (the operator's ``--check-etsy``).

Errors stored before today's wording (before 758c2d4) are re-explained when shown
(``plain_error``). Everything here is read from our own database (no Etsy call).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    ConnectionStatus,
    DraftAttempt,
    DraftDismissal,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    ListingSnapshot,
)
from app.etsy.errors import foreign_setting
from app.pipeline import design_match
from app.pipeline import links as L
from app.pipeline import personalization as P
from app.pipeline import profile_shops

#: The step a job's error names ("... while setting sizes, prices and SKU ...", etsy/errors.py).
_STEP = re.compile(r"\bwhile ([A-Za-z ,'-]+?)(?:[:;()]|$)")
#: What explain() said about a refused inventory update before it named the cause:
#: Etsy answers 404 when the inventory carries another shop's processing profile.
_OLD_INVENTORY_404 = "could not find the listing while setting sizes, prices and SKU"
#: The listing id in the path of a refused request ("/application/listings/4596864790/inventory").
_LISTING_IN_PATH = re.compile(r"/listings/(\d+)")
#: Steps that only happen once the listing exists on Etsy (etsy/errors.py ``_STEPS``).
AFTER_CREATE = frozenset({
    "adding an image", "removing an image", "setting sizes, prices and SKU", "setting a category attribute",
    "setting the personalization question", "updating the listing", "reading the listing back",
    "reading the listing's images",
})

# --- categories -------------------------------------------------------------------------------------

FINISH = "finish"
FIX_PERSONALIZATION = "fix_personalization"
FIX_SHOP = "fix_shop"
REPLACED = "replaced"
BATCH_DELETED = "batch_deleted"
NOT_LINKED = "not_linked"
NOTHING = "nothing"
NOT_ON_ETSY = "not_on_etsy"
UNCHECKED = "unchecked"

LABELS: dict[str, str] = {
    FINISH: "Finish now",
    FIX_PERSONALIZATION: "Fix personalization, then finish",
    FIX_SHOP: "Set up the shop, then finish",
    REPLACED: "Replaced: delete on Etsy",
    BATCH_DELETED: "Batch deleted: delete on Etsy, or finish it in Shop Manager",
    NOT_LINKED: "On Etsy, not linked",
    NOTHING: "Nothing to do",
    NOT_ON_ETSY: "Not on Etsy",
    UNCHECKED: "No listing id stored: look on Etsy (--check-etsy)",
}
#: The order of the operator's summary.
ORDER: tuple[str, ...] = (FINISH, FIX_PERSONALIZATION, FIX_SHOP, REPLACED, BATCH_DELETED, NOT_LINKED, NOTHING,
                          NOT_ON_ETSY, UNCHECKED)
#: What the seller's list shows (each with an Etsy listing): finish these, fix these, delete these on Etsy.
SELLER: frozenset[str] = frozenset({FINISH, FIX_PERSONALIZATION, FIX_SHOP, REPLACED, BATCH_DELETED})
#: What the seller can take off the list once it is deleted on Etsy.
DISMISSABLE: frozenset[str] = frozenset({REPLACED, BATCH_DELETED})


@dataclass
class DraftRecord:
    connection_id: uuid.UUID
    shop_name: str
    #: How the record is addressed (its draft attempt's id, else "job-<id>" of its latest job).
    record_id: str
    attempt_id: uuid.UUID | None = None
    #: The listing text in the app; None when it is gone (its batch was deleted).
    content_id: uuid.UUID | None = None
    content_gone: bool = False
    batch_id: uuid.UUID | None = None
    group: str | None = None
    sku: str | None = None
    title: str = ""
    keys: design_match.Keys = field(default_factory=design_match.Keys)
    etsy_listing_id: int | None = None
    #: Where the listing id was found ("draft attempt", "create snapshot", "Etsy's answer",
    #: "found on Etsy"), or None: never stored.
    id_source: str | None = None
    #: A create request was sent (so a listing may exist even without an id).
    create_sent: bool = False
    started_at: datetime | None = None
    jobs: list[Job] = field(default_factory=list)
    #: The latest failure: the step, what the seller is told, Etsy's kept answer.
    step: str | None = None
    error: str | None = None
    last_error: str | None = None
    etsy_error: dict[str, Any] | None = None
    failed_at: datetime | None = None
    job_profile_id: str | None = None
    #: A job is finishing it now.
    finishing: bool = False
    #: This listing's draft finished since; or (no listing id) the design was drafted since.
    finished: bool = False
    redone_by: int | None = None
    #: Another draft of the same design in this shop: delete this one on Etsy.
    replaced_by: int | None = None
    replaced_key: str | None = None
    #: Why it cannot be finished yet (a setting this shop lacks), and whether one click sets it up.
    blocked: str | None = None
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    setup: bool = False
    #: Etsy would refuse the listing's personalization question as it stands (fix it first).
    personalization_problems: list[str] = field(default_factory=list)
    #: The seller took it off the list (they deleted it on Etsy).
    dismissed: bool = False
    #: What the operator's look at Etsy found: "draft", "active", ... or "gone"; None: not looked.
    etsy_state: str | None = None
    checked: bool = False
    #: How many raw records (failed jobs of other listing texts, older tries) were merged in.
    merged: int = 0
    category: str = UNCHECKED

    @property
    def label(self) -> str:
        return LABELS[self.category]

    @property
    def latest(self) -> datetime | None:
        times = [t for t in (self.failed_at, self.started_at) if t is not None]
        return max(times) if times else None


def step_of(error: str | None) -> str | None:
    match = _STEP.search(error or "")
    return match.group(1).strip() if match else None


_OLD_CODE = re.compile(r"^(?:client_error:|etsy client error \()(\d{3})")
_OLD_SERVER = re.compile(r"^etsy server error \((\d{3})\)")


def plain_error(error: str | None, shop: str) -> str | None:
    """The stored error, in today's words (etsy/errors.py ``explain``). Errors stored before
    758c2d4 said "Etsy could not find the listing" for a refused inventory (the listing is
    there, the processing profile was not), quoted Etsy's refusal of another shop's id raw,
    or gave only a status code; they are re-explained here as a new failure would be."""
    if not error:
        return error
    low = error.lower().strip()
    if "calculated shipping profile" in low:
        # Stored before the weight and size were read and sent (2026-10-22): the next try
        # carries them; if the reference really has none, it says what to add.
        from app.pipeline.reference import SIZE_FIX

        return ("Etsy needed the item's weight and size for its calculated shipping profile. "
                "They are read from the reference listing now: finish the draft. " + SIZE_FIX)
    if _OLD_INVENTORY_404.lower() in low:  # (compared in lower case: "SKU" never matched before)
        return (f"Etsy refused this draft's sizes, prices and SKU: they carried another shop's processing "
                f"profile, which {shop} does not have. Set it up in {shop}, then finish the draft.")
    setting = foreign_setting(error)
    if setting and "does not have the" not in low:
        step = step_of(error)
        where = f" (Etsy refused it while {step})" if step else ""
        return f"{shop} does not have the {setting} this listing used; set it up in {shop}, then finish the draft{where}"
    code = _OLD_CODE.match(low)
    if code:
        return f"Etsy refused a step of this draft (it answered {code.group(1)}) without saying why. Finishing tries it again."
    server = _OLD_SERVER.match(low)
    if server:
        return (f"Etsy had a problem of its own (it answered {server.group(1)}); nothing is wrong with the listing. "
                "Finishing carries on where it stopped.")
    if low == "max_attempts_exceeded":
        return "This stopped after several tries. Finishing carries on where it stopped."
    return error


def design_key(batch_id: uuid.UUID | None, group_key: str | None) -> tuple[str, str] | None:
    """A design: its batch and listing group (the same design rewritten keeps both). None
    without a group: two listings are never taken for one design by guesswork."""
    return (str(batch_id), group_key) if batch_id and group_key else None


def listing_in_path(path: str | None) -> int | None:
    match = _LISTING_IN_PATH.search(path or "")
    return int(match.group(1)) if match else None


def _aware(at: datetime | None) -> datetime | None:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


def categorize(r: DraftRecord) -> str:
    """The record's action, from what is known about it (and what a look at Etsy found)."""
    if r.finished:
        return NOTHING
    if r.etsy_listing_id is None:
        if r.redone_by is not None:
            return NOTHING
        if r.checked or (not r.create_sent and r.step not in AFTER_CREATE):
            return NOT_ON_ETSY  # looked and not there, or it stopped before anything was created
        return UNCHECKED
    if r.etsy_state == "gone":
        return NOTHING if (r.replaced_by or r.content_gone) else NOT_ON_ETSY
    if r.replaced_by is not None:
        return REPLACED
    if r.etsy_state not in (None, "draft"):
        return NOTHING  # active (published in Shop Manager) or otherwise not a draft any more
    if r.content_gone:
        return BATCH_DELETED
    if r.personalization_problems:
        return FIX_PERSONALIZATION
    if r.blocked:
        return FIX_SHOP
    return FINISH


@dataclass
class _Pub:
    listing_id: int
    created_at: datetime | None
    keys: design_match.Keys


async def records(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    batch_id: uuid.UUID | None = None,
    since: date | None = None,
    with_blocks: bool = True,
) -> list[DraftRecord]:
    """Every failed or half-made draft of the account's connected shops, merged per design
    and shop and given its category, oldest first. ``since``: only what happened on or
    after that day (UTC). ``with_blocks``: also whether the shop's setup lets each be
    finished now. Read only."""
    from app.pipeline.targets import resolve_target  # late: targets imports a lot

    shops = {c.id: c for c in (await session.execute(
        select(EtsyConnection).where(EtsyConnection.tenant_id == tenant_id, EtsyConnection.status == ConnectionStatus.active)
    )).scalars()}
    if not shops:
        return []
    jobs = list((await session.execute(
        select(Job).where(Job.tenant_id == tenant_id, Job.type == JobType.create_draft, Job.connection_id.in_(list(shops)))
        .order_by(Job.created_at)
    )).scalars())
    by_pair: dict[tuple[uuid.UUID, str], list[Job]] = defaultdict(list)
    for job in jobs:
        by_pair[(job.connection_id, str((job.payload or {}).get("content_id")))].append(job)
    # Create snapshots: the listing id the moment Etsy returned it, per job (survive the batch).
    snaps: dict[uuid.UUID, ListingSnapshot] = {}
    for snap in (await session.execute(
        select(ListingSnapshot).join(Job, Job.id == ListingSnapshot.job_id)
        .where(ListingSnapshot.tenant_id == tenant_id, Job.tenant_id == tenant_id, Job.type == JobType.create_draft)
        .order_by(ListingSnapshot.taken_at)
    )).scalars():
        if (snap.payload or {}).get("operation") == "create_draft":
            snaps[snap.job_id] = snap
    attempts = {(a.connection_id, str(a.content_id)): a for a in (await session.execute(
        select(DraftAttempt).where(DraftAttempt.tenant_id == tenant_id, DraftAttempt.connection_id.in_(list(shops)))
    )).scalars()}
    content_ids = {uuid.UUID(c) for _, c in set(by_pair) | set(attempts) if _is_uuid(c)}
    contents: dict[uuid.UUID, tuple[GeneratedContent, str | None, str | None]] = {}
    ids = sorted(content_ids)
    for start in range(0, len(ids), 500):
        for content, group, sku in (await session.execute(
            select(GeneratedContent, Asset.group_key, Asset.parsed_sku)
            .outerjoin(Asset, Asset.id == GeneratedContent.asset_id)
            .where(GeneratedContent.tenant_id == tenant_id, GeneratedContent.id.in_(ids[start:start + 500]))
        )).all():
            contents[content.id] = (content, group, sku)
    pubs: dict[uuid.UUID, list[_Pub]] = defaultdict(list)
    published_ids: dict[uuid.UUID, set[int]] = defaultdict(set)
    published_content: dict[tuple[uuid.UUID, str], int] = {}
    for pub, batch, group, parsed in (await session.execute(
        select(ListingPublication, GeneratedContent.batch_id, Asset.group_key, Asset.parsed_sku)
        .outerjoin(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .outerjoin(Asset, Asset.id == GeneratedContent.asset_id)
        .where(ListingPublication.tenant_id == tenant_id, ListingPublication.state != "deleted_on_etsy",
               ListingPublication.connection_id.in_(list(shops)))
    )).all():
        lid = int(pub.etsy_listing_id)
        published_ids[pub.connection_id].add(lid)
        if pub.content_id is not None:
            published_content[(pub.connection_id, str(pub.content_id))] = lid
        pubs[pub.connection_id].append(_Pub(lid, _aware(pub.created_at), design_match.keys(
            content=pub.content_id, batch=batch, group=group, skus=[pub.sku, parsed], title=pub.title)))
    dismissed = set((await session.execute(
        select(DraftDismissal.connection_id, DraftDismissal.etsy_listing_id).where(DraftDismissal.tenant_id == tenant_id)
    )).all())

    raw: list[DraftRecord] = []
    for pair in sorted(set(by_pair) | set(attempts), key=str):
        shop_id, content_key = pair
        pair_jobs = by_pair.get(pair, [])
        failed = [j for j in pair_jobs if j.status is JobStatus.failed]
        attempt = attempts.get(pair)
        if attempt is None and not failed:
            continue
        if attempt is not None and attempt.etsy_listing_id is None and attempt.create_sent_at is None and not failed:
            continue
        shop = shops[shop_id]
        name = shop.shop_name or f"shop {shop.shop_id}"
        found = contents.get(uuid.UUID(content_key)) if _is_uuid(content_key) else None
        content, group, sku = found if found is not None else (None, None, None)
        latest_failed = failed[-1] if failed else None
        latest_job = latest_failed or (pair_jobs[-1] if pair_jobs else None)
        snap = next((snaps[j.id] for j in reversed(pair_jobs) if j.id in snaps), None)
        record = DraftRecord(
            connection_id=shop_id, shop_name=name,
            record_id=str(attempt.id) if attempt is not None else f"job-{latest_job.id}",  # type: ignore[union-attr]
            attempt_id=attempt.id if attempt is not None else None,
            content_id=content.id if content is not None else None,
            content_gone=content is None,
            batch_id=content.batch_id if content is not None else (latest_job.batch_id if latest_job else None),
            group=group, sku=sku, jobs=list(failed),
            finishing=any(j.status in (JobStatus.queued, JobStatus.running) for j in pair_jobs),
        )
        # The listing id: the attempt holds it while there is one (it is reset when the
        # listing was found gone, so an older snapshot then says nothing); else the snapshot,
        # else the path of the refused request.
        if attempt is not None:
            if attempt.etsy_listing_id is not None:
                record.etsy_listing_id, record.id_source = int(attempt.etsy_listing_id), "draft attempt"
            record.create_sent = attempt.create_sent_at is not None or attempt.etsy_listing_id is not None
        else:
            if snap is not None:
                record.etsy_listing_id, record.id_source = int(snap.listing_id), "create snapshot"
            else:
                in_path = next((listing_in_path(((j.payload or {}).get("etsy_error") or {}).get("path"))
                                for j in reversed(failed) if listing_in_path(((j.payload or {}).get("etsy_error") or {}).get("path"))), None)
                if in_path is not None:
                    record.etsy_listing_id, record.id_source = in_path, "Etsy's answer"
            record.create_sent = record.etsy_listing_id is not None
        submitted = ((snap.payload or {}).get("submitted") or {}) if snap is not None else {}
        record.title = (attempt.title if attempt is not None else None) or submitted.get("title") \
            or (content.title if content is not None else None) or ""
        times = [_aware(t) for t in (attempt.created_at if attempt is not None else None,
                                      failed[0].created_at if failed else None,
                                      snap.taken_at if snap is not None else None) if t is not None]
        record.started_at = min(times) if times else None
        if latest_failed is not None:
            record.last_error = latest_failed.last_error
            record.etsy_error = (latest_failed.payload or {}).get("etsy_error")
            record.step = (record.etsy_error or {}).get("step") or step_of(latest_failed.last_error)
            record.error = plain_error(latest_failed.last_error, name)
            record.failed_at = _aware(latest_failed.finished_at or latest_failed.created_at)
            record.job_profile_id = (latest_failed.payload or {}).get("profile_id")
        record.keys = design_match.keys(content=record.content_id, batch=record.batch_id, group=group, skus=[sku],
                                        title=record.title)
        if record.etsy_listing_id is not None and record.etsy_listing_id in published_ids[shop_id]:
            record.finished = True
        elif record.etsy_listing_id is None and pair in published_content:
            record.redone_by = published_content[pair]
        if since is not None:
            start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
            if record.latest is None or record.latest < start:
                continue
        raw.append(record)

    out = _merge(raw)
    for record in out:
        _supersede(record, pubs.get(record.connection_id, []))
        record.dismissed = record.etsy_listing_id is not None and (record.connection_id, record.etsy_listing_id) in dismissed
    if batch_id is not None:
        out = [r for r in out if r.batch_id == batch_id]
    for record in out:
        found = contents.get(record.content_id) if record.content_id else None
        # (Also without a listing id: the operator's look at Etsy may find its draft.)
        if found is not None and not (record.finished or record.replaced_by or record.redone_by):
            content = found[0]
            profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
            setting, _ = P.for_listing(content.personalization, profile.personalization if profile else None,
                                       profile.cached_payload if profile else None)
            record.personalization_problems = P.etsy_problems(setting)
            if with_blocks:
                target = await resolve_target(session, content, shops[record.connection_id])
                if not target.ok and not target.stale:  # a stale profile is refreshed by the job itself
                    state = profile_shops.state_of(target.profile, target.link) if target.profile and target.link else None
                    if state is not None and state.missing:
                        record.blocked = L.setup_message(next(iter(state.missing)), record.shop_name)
                    else:
                        record.blocked = target.reason or "no profile for this shop"
                    record.setup = bool(target.setup or (state is not None and state.missing))
                    if target.profile is not None:
                        record.profile_id, record.profile_name = target.profile.id, target.profile.name
        record.category = categorize(record)
    out.sort(key=lambda r: (r.started_at or datetime.min.replace(tzinfo=timezone.utc), r.record_id))
    return out


def _is_uuid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except (ValueError, TypeError):
        return False
    return True


def _absorb(into: DraftRecord, other: DraftRecord) -> None:
    into.jobs.extend(j for j in other.jobs if j not in into.jobs)
    into.merged += 1 + other.merged
    into.finishing = into.finishing or other.finishing
    if other.started_at is not None and (into.started_at is None or other.started_at < into.started_at):
        into.started_at = other.started_at
    if other.failed_at is not None and (into.failed_at is None or other.failed_at > into.failed_at):
        # The newest failure says what went wrong last.
        into.failed_at, into.step, into.error = other.failed_at, other.step or into.step, other.error or into.error
        into.last_error, into.etsy_error = other.last_error or into.last_error, other.etsy_error or into.etsy_error
    for attr in ("batch_id", "group", "sku"):
        if getattr(into, attr) is None and getattr(other, attr) is not None:
            setattr(into, attr, getattr(other, attr))


def _when(r: DraftRecord) -> datetime:
    return r.started_at or datetime.min.replace(tzinfo=timezone.utc)


def _primary(cluster: list[DraftRecord]) -> DraftRecord:
    """The record a cluster is kept as: the newest unfinished one with a listing that the
    app can still finish (its text is there), else the newest with a listing, else the newest."""
    for pool in (
        [r for r in cluster if r.etsy_listing_id and not r.finished and not r.content_gone],
        [r for r in cluster if r.etsy_listing_id and not r.finished],
        [r for r in cluster if r.etsy_listing_id],
        cluster,
    ):
        if pool:
            return max(pool, key=lambda r: (_when(r), r.record_id))
    raise AssertionError("empty cluster")


def _merge(raw: list[DraftRecord]) -> list[DraftRecord]:
    """One record per listing and shop, then per design and shop (pipeline/design_match.py)."""
    # 1) The same Etsy listing in one shop is one record.
    by_listing: dict[tuple[uuid.UUID, int], DraftRecord] = {}
    rest: list[DraftRecord] = []
    for r in sorted(raw, key=lambda r: (r.attempt_id is None, r.content_gone, -_when(r).timestamp())):
        if r.etsy_listing_id is None:
            rest.append(r)
            continue
        key = (r.connection_id, r.etsy_listing_id)
        if key in by_listing:
            _absorb(by_listing[key], r)
        else:
            by_listing[key] = r
    pool = list(by_listing.values()) + rest
    # 2) The same design in one shop: clusters of records any two of which match.
    out: list[DraftRecord] = []
    for shop_id in {r.connection_id for r in pool}:
        mine = [r for r in pool if r.connection_id == shop_id]
        parent = list(range(len(mine)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(mine)):
            for j in range(i + 1, len(mine)):
                if design_match.same(mine[i].keys, mine[j].keys) is not None:
                    parent[find(i)] = find(j)
        clusters: dict[int, list[DraftRecord]] = defaultdict(list)
        for i, r in enumerate(mine):
            clusters[find(i)].append(r)
        for cluster in clusters.values():
            keep = _primary(cluster)
            out.append(keep)
            for r in cluster:
                if r is keep:
                    continue
                if r.etsy_listing_id is not None and r.finished:
                    out.append(r)  # its own draft finished: a record of its own ("nothing to do")
                elif r.etsy_listing_id is not None and r.etsy_listing_id != keep.etsy_listing_id and keep.etsy_listing_id:
                    # Another half-made draft of the same design here: one is finished, this one goes.
                    match = design_match.same(r.keys, keep.keys) or design_match.Match("same design")
                    r.replaced_by, r.replaced_key = keep.etsy_listing_id, f"another draft of the same design ({match})"
                    out.append(r)
                else:
                    _absorb(keep, r)  # a failure of the same design with no listing of its own
    return out


def _supersede(record: DraftRecord, pubs: list[_Pub]) -> None:
    """A draft of the same design that succeeded in this shop after this one began."""
    if record.finished:
        return
    best: tuple[int, float, _Pub, design_match.Match] | None = None
    for pub in pubs:
        if record.etsy_listing_id is not None and pub.listing_id == record.etsy_listing_id:
            continue
        if record.started_at is not None and pub.created_at is not None and pub.created_at < record.started_at:
            continue
        match = design_match.same(record.keys, pub.keys)
        if match is None:
            continue
        score = (design_match.rank(match), -(pub.created_at.timestamp() if pub.created_at else 0.0))
        if best is None or score < best[:2]:
            best = (*score, pub, match)  # type: ignore[assignment]
    if best is None:
        return
    pub, match = best[2], best[3]
    if record.etsy_listing_id is not None:
        record.replaced_by, record.replaced_key = pub.listing_id, str(match)
    else:
        record.redone_by = pub.listing_id


async def half_made(
    session: AsyncSession, tenant_id: uuid.UUID, *, batch_id: uuid.UUID | None = None, with_blocks: bool = True,
) -> list[DraftRecord]:
    """The seller's list: drafts that exist on Etsy and need something from them (finish,
    fix then finish, delete on Etsy), not the ones they took off the list."""
    return [r for r in await records(session, tenant_id, batch_id=batch_id, with_blocks=with_blocks)
            if r.etsy_listing_id is not None and r.category in SELLER and not r.dismissed]


#: Seconds either side of a failure in which an untracked draft is looked for on Etsy.
WINDOW = timedelta(minutes=30)
