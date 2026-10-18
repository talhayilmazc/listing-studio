"""``python -m app.cli draft-failures <email> [--since YYYY-MM-DD] [--check-etsy]``: one
account's failed and half-made drafts, for diagnosis on the server.

The records are pipeline/unfinished.py's: one per design and shop (the failed jobs of
one listing, older tries and duplicates merged), each with its action. A summary comes
first: per action, how many and which Etsy listing ids ("Finish now", "Fix
personalization, then finish", "Replaced: delete on Etsy", "On Etsy, not linked",
"Nothing to do", ...). Then each record:

* the shop and the Etsy listing id, and where the id was found (draft attempt, create
  snapshot, Etsy's answer, found on Etsy), or "never stored";
* the design (batch, group, SKU), the step that failed and Etsy's answer (status, reason
  and the shape of what was sent, never the seller's words), or the stored message for
  failures from before Etsy's answer was kept;
* for a personalization failure, the question as it would be sent now and what Etsy's
  rules say about it;
* whether a later draft of the same design replaced it, and by which key (same listing,
  same design, SKU, group, title).

``--check-etsy`` (read only, run on the server): each record's listing is read in its own
shop (getListing: draft, active, or gone); for a shop with records that have no listing
id, the shop's drafts created within 30 minutes of each failure are read (getListingsByShop,
state=draft, newest first, only as many pages as that takes) and matched to those records
by SKU, group or title; a draft there that nothing in the app knows is printed as "On
Etsy, not linked". Every request goes through the app's rate limiter and daily budget
(as upkeep) and is counted; nothing is written to Etsy and nothing is deleted. Reading a
shop's key may renew it (the OAuth exchange); the key is never printed.

The report itself only SELECTs (on Postgres in a READ ONLY transaction, rolled back) and
only the named account's rows. No token, no buyer data.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logsafety import redact
from app.db.models import (
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobType,
    ListingProfile,
    ListingPublication,
    ListingSnapshot,
    Tenant,
)
from app.etsy.errors import shape_of
from app.pipeline import design_match, personalization, unfinished

#: Pages of a shop's drafts read at most per shop (100 a page, newest first).
MAX_DRAFT_PAGES = 10


@dataclass
class NotLinked:
    shop: str
    listing_id: int
    created: datetime | None
    skus: list[str] = field(default_factory=list)


@dataclass
class EtsyCheck:
    calls: int = 0
    not_linked: list[NotLinked] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def report(
    sm: async_sessionmaker,
    email: str,
    since: date | None = None,
    *,
    check_etsy: bool = False,
    client_factory: Callable | None = None,
    token_service: Any = None,
) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            found = await _gather(session, email, since)
            if isinstance(found, str):
                return found
            tenant, records, known, details = found
            who = (tenant.email, str(tenant.id))  # read before the rollback expires the row
        finally:
            await session.rollback()
    check = None
    if check_etsy:
        check = await etsy_check(sm, records, known, client_factory=client_factory, token_service=token_service)
    return _render(who, since, records, details, check)


def _when(at: datetime | None) -> str:
    if at is None:
        return "?"
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _shape(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else "not recorded"


async def _gather(session: AsyncSession, email: str, since: date | None):  # noqa: ANN202
    tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
    if tenant is None:
        return f"no account with the address {email}"
    records = await unfinished.records(session, tenant.id, since=since, with_blocks=True)
    # Every listing id the app knows per shop: an Etsy draft among these is not "not linked".
    known: dict[uuid.UUID, set[int]] = defaultdict(set)
    for shop_id, lid in (await session.execute(
        select(ListingPublication.connection_id, ListingPublication.etsy_listing_id).where(ListingPublication.tenant_id == tenant.id)
    )).all():
        known[shop_id].add(int(lid))
    for shop_id, lid in (await session.execute(
        select(DraftAttempt.connection_id, DraftAttempt.etsy_listing_id)
        .where(DraftAttempt.tenant_id == tenant.id, DraftAttempt.etsy_listing_id.is_not(None))
    )).all():
        known[shop_id].add(int(lid))
    for shop_id, lid in (await session.execute(
        select(Job.connection_id, ListingSnapshot.listing_id).join(Job, Job.id == ListingSnapshot.job_id)
        .where(ListingSnapshot.tenant_id == tenant.id, Job.tenant_id == tenant.id, Job.type == JobType.create_draft)
    )).all():
        if shop_id is not None:
            known[shop_id].add(int(lid))
    for r in records:
        if r.etsy_listing_id is not None:
            known[r.connection_id].add(r.etsy_listing_id)
    # The personalization question as it would be sent now, for personalization failures.
    details: dict[str, list[str]] = {}
    for r in records:
        if not (r.step and "personalization" in r.step and r.content_id):
            continue
        content = await session.get(GeneratedContent, r.content_id)
        if content is None or content.tenant_id != tenant.id:
            continue
        profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
        setting, source = personalization.for_listing(
            content.personalization, profile.personalization if profile else None, profile.cached_payload if profile else None)
        if setting and setting.get("enabled"):
            body = {"personalization_questions": personalization.questions_for(setting)}
            problems = personalization.etsy_problems(setting)
            details[r.record_id] = [
                f"  personalization as it would be sent now ({source}): {_shape(shape_of(body))}",
                "  Etsy's rules: " + ("; ".join(problems) if problems else "it meets them"),
            ]
        else:
            details[r.record_id] = [f"  personalization now: off ({source})"]
    return tenant, records, known, details


_created = design_match.created_of
etsy_keys = design_match.etsy_keys


def _default_client(shop_id: uuid.UUID, http):  # noqa: ANN001, ANN202
    from app.cli import _client_for_check

    return _client_for_check(shop_id, http)


def _default_tokens():  # noqa: ANN202
    from app.cli import _token_service

    return _token_service()


async def etsy_check(
    sm: async_sessionmaker,
    records: list[unfinished.DraftRecord],
    known: dict[uuid.UUID, set[int]],
    *,
    client_factory: Callable | None = None,
    token_service: Any = None,
) -> EtsyCheck:
    """Read only: each record's listing in its own shop, and the drafts near each failure
    of a record without a listing id (see the module). Updates the records (state, a draft
    found for one) and their category; returns the calls made and the drafts nothing knows."""
    import httpx

    from app.etsy.api import RateLimitExceeded
    from app.etsy.errors import EtsyClientError
    from app.etsy.guard import EtsyCallsBlocked

    check = EtsyCheck()
    factory = client_factory or _default_client
    tokens = token_service or _default_tokens()
    by_shop: dict[uuid.UUID, list[unfinished.DraftRecord]] = defaultdict(list)
    for r in records:
        if not r.finished:
            by_shop[r.connection_id].append(r)
    async with httpx.AsyncClient(timeout=30.0) as http:
        for shop_id, items in by_shop.items():
            name = items[0].shop_name
            try:
                async with sm() as session:  # writable: an expired key is renewed (the OAuth exchange)
                    shop = await session.get(EtsyConnection, shop_id)
                    if shop is None or (shop.access_token_enc is None and shop.refresh_token_enc is None):
                        check.notes.append(f"{name}: not checked (no Etsy key is stored for this shop; it needs reconnecting)")
                        continue
                    token = await tokens.get_valid_access_token(session, shop)
                    await session.commit()
                client = factory(shop_id, http)
                kw = {"access_token": token, "tenant_id": shop.tenant_id}
                for r in items:
                    if r.etsy_listing_id is None:
                        continue
                    check.calls += 1
                    try:
                        row = await client.get_listing(r.etsy_listing_id, **kw)
                        r.etsy_state = str(row.get("state") or "unknown")
                    except EtsyClientError as exc:
                        if exc.status_code in (404, 410):
                            r.etsy_state = "gone"
                        else:
                            check.notes.append(f"{name}: listing {r.etsy_listing_id} could not be read (Etsy answered {exc.status_code})")
                    r.checked = True
                missing = [r for r in items if r.etsy_listing_id is None and r.redone_by is None]
                if missing and shop.shop_id:
                    await _drafts_near(client, kw, shop.shop_id, name, missing, known.get(shop_id, set()), check)
            except RateLimitExceeded:
                check.notes.append("stopped: the app's daily Etsy budget is used up (nothing more was read)")
                break
            except EtsyCallsBlocked:
                check.notes.append("Etsy calls are blocked here (ETSY_CALLS_BLOCKED): run this on the server")
                break
            except Exception as exc:  # noqa: BLE001 - one shop's trouble does not hide the others
                check.notes.append(f"{name}: not checked ({type(exc).__name__}: {redact(str(exc))[:160]})")
    for r in records:
        r.category = unfinished.categorize(r)
    return check


async def _drafts_near(client, kw: dict, etsy_shop_id: int, name: str, missing: list[unfinished.DraftRecord],  # noqa: ANN001
                       known: set[int], check: EtsyCheck) -> None:
    windows = []
    for r in missing:
        at = r.failed_at or r.started_at
        if at is not None:
            windows.append((r, at - unfinished.WINDOW, at + unfinished.WINDOW))
    if not windows:
        return
    earliest = min(lo for _, lo, _ in windows)
    rows: list[dict[str, Any]] = []
    for page in range(MAX_DRAFT_PAGES):
        check.calls += 1
        got = (await client.get_listings_by_shop(etsy_shop_id, state="draft", limit=100, offset=page * 100, **kw)).get("results") or []
        rows += [g for g in got if isinstance(g, dict)]
        times = [t for t in (_created(g) for g in got) if t is not None]
        if len(got) < 100 or (times and min(times) < earliest):
            break
    else:
        check.notes.append(f"{name}: read {MAX_DRAFT_PAGES} pages of drafts and stopped; older drafts were not read")
    near = [g for g in rows if int(g.get("listing_id") or 0) not in known
            and any(lo <= (_created(g) or lo) <= hi for _, lo, hi in windows)]
    for r, lo, hi in windows:
        best = None
        for g in near:
            made = _created(g)
            if made is not None and not lo <= made <= hi:
                continue
            match = design_match.same(r.keys, etsy_keys(g))
            if match is not None and (best is None or design_match.rank(match) < design_match.rank(best[1])):
                best = (g, match)
        r.checked = True
        if best is not None:
            g, match = best
            near.remove(g)
            r.etsy_listing_id, r.id_source = int(g["listing_id"]), f"found on Etsy (matched by {match})"
            r.etsy_state, r.create_sent = str(g.get("state") or "draft"), True
            known.add(r.etsy_listing_id)
    for g in near:
        check.not_linked.append(NotLinked(name, int(g["listing_id"]), _created(g),
                                          sorted({str(s) for s in g.get("skus") or [] if s})))


def _render(who: tuple[str, str], since: date | None, records: list[unfinished.DraftRecord], details: dict[str, list[str]],
            check: EtsyCheck | None) -> str:
    lines = [f"Account {who[0]} ({who[1][:8]}): failed and half-made drafts"
             + (f" since {since.isoformat()}" if since else ""), ""]
    if not records and not (check and check.not_linked):
        lines.append("None.")
        if check is not None:
            lines.append(f"Etsy calls made: {check.calls} (read only)")
        return "\n".join(lines)

    # --- summary: per action, how many and which listing ids ------------------------------------
    by: dict[str, list[unfinished.DraftRecord]] = defaultdict(list)
    for r in records:
        if not r.dismissed:
            by[r.category].append(r)
    merged = sum(r.merged for r in records)
    lines.append(f"Summary: {len(records)} record(s), one per design and shop"
                 + (f" ({merged} duplicate record(s) merged in)" if merged else ""))
    for category in unfinished.ORDER:
        if category == unfinished.NOT_LINKED:
            items = [f"{n.shop} {n.listing_id}" for n in (check.not_linked if check else [])]
            count = len(items)
        else:
            group = by.get(category, [])
            count = len(group)
            items = [f"{r.shop_name} {r.etsy_listing_id}" for r in group if r.etsy_listing_id]
        if category in (unfinished.NOT_LINKED, unfinished.UNCHECKED, unfinished.NOT_ON_ETSY) and not count:
            if not (category == unfinished.NOT_LINKED and check is not None):
                continue
        lines.append(f"  {unfinished.LABELS[category]}: {count}" + (f" — {', '.join(items)}" if items else ""))
    dismissed = [r for r in records if r.dismissed]
    if dismissed:
        lines.append(f"  Taken off the seller's list (deleted on Etsy, they said): {len(dismissed)} — "
                     + ", ".join(f"{r.shop_name} {r.etsy_listing_id}" for r in dismissed))
    if check is not None:
        lines.append(f"  Etsy calls made: {check.calls} (read only, through the rate limiter and daily budget)")
        lines += [f"  note: {n}" for n in check.notes]
    lines.append("")

    # --- each record ---------------------------------------------------------------------------
    for r in records:
        if r.etsy_listing_id is not None:
            listing = f"listing {r.etsy_listing_id} (id from {r.id_source})"
        elif r.create_sent or r.step in unfinished.AFTER_CREATE:
            listing = "listing id: never stored (no draft attempt, create snapshot or Etsy answer has it)"
        else:
            listing = "not created on Etsy (it stopped before the create request)"
        lines.append(f"- {r.shop_name} · {listing} · failed {_when(r.failed_at)}"
                     + (f" · {r.merged} more merged in" if r.merged else ""))
        lines.append(f"  action: {r.label}" + (" (taken off the seller's list)" if r.dismissed else ""))
        lines.append(f"  design: {r.keys.label}" + (" · its batch was deleted" if r.content_gone else ""))
        lines.append(f"  step: {r.step or 'not recorded'}")
        if r.etsy_error:
            record = r.etsy_error
            lines.append(f"  Etsy answered: {record.get('status')} {record.get('method') or ''} {record.get('path') or ''}".rstrip())
            lines.append(f"  Etsy's reason: {redact(record.get('message') or 'none given')}")
            lines.append(f"  sent: {_shape(record.get('sent'))}")
        elif r.last_error is not None:
            lines.append(f"  stored message (before Etsy's answer was kept): {redact(r.last_error) or '-'}")
            if r.error and r.error != r.last_error:
                lines.append(f"  shown to the seller now: {redact(r.error)}")
            lines.append("  sent: not recorded (failed before request shapes were kept)")
        lines += details.get(r.record_id, [])
        if r.personalization_problems and not details.get(r.record_id):
            lines.append("  Etsy's rules: " + "; ".join(r.personalization_problems))
        if r.blocked:
            lines.append(f"  shop setup: {r.blocked}")
        if r.replaced_by:
            lines.append(f"  superseded: yes, by listing {r.replaced_by} (matched by {r.replaced_key})")
        elif r.redone_by:
            lines.append(f"  superseded: drafted again since as listing {r.redone_by}")
        elif r.finished:
            lines.append("  superseded: finished since (this listing's own draft succeeded)")
        else:
            lines.append("  superseded: no")
        if r.checked:
            lines.append(f"  on Etsy: {r.etsy_state or ('no matching draft found' if r.etsy_listing_id is None else 'not read')}")
        lines.append("")
    if check is not None and check.not_linked:
        lines.append("On Etsy, not linked (drafts made near a failure that nothing in the app knows):")
        for n in check.not_linked:
            lines.append(f"- {n.shop} · listing {n.listing_id} · created {_when(n.created)}"
                         + (f" · SKU {', '.join(n.skus)}" if n.skus else ""))
        lines.append("")
    return "\n".join(lines)
