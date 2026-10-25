"""``python -m app.cli publish-report <email> [--since YYYY-MM-DD] [--check-etsy]``: where each
listing of one account stands between its draft and going live, for diagnosis on the server.

A summary comes first: per outcome, how many listings and which (Etsy listing id, or the
listing's short id when it has no draft), split by personalization on and off:

* published; published here but still a draft on Etsy (``--check-etsy``);
* approved but not published; scheduled and waiting; scheduled and missed;
* never approved; refused by Etsy; held by our own check.

Then one block per listing and shop:

* the shop and Etsy listing id, the title's first 40 characters and the SKU;
* personalization: on or off, question type, required, character limit, the instructions'
  length and Etsy's rules (120 characters, one word in capitals), never the text itself;
* approval (when and by which path: the card, or "Approve all"; "not recorded" before 0091),
  the group-schedule slot and its state, the draft's own schedule and its state, and every
  draft and go-live job with its time, result, our reason and Etsy's stored answer;
* every finding and warning on the listing (rule and severity, never the seller's words), and
  whether it blocks drafting or going live;
* with ``--check-etsy``, the listing's state on Etsy (draft, active, gone), read through the
  app's rate limiter and daily budget as upkeep; nothing is written to Etsy.

The report itself only SELECTs (on Postgres in a READ ONLY transaction, rolled back), and
only the named account's rows. No token, no buyer data, no seller text but the title's start.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logsafety import redact
from app.db.models import (
    Asset,
    ComplianceFinding,
    ComplianceSeverity,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.etsy.scheduling import MISSED_AFTER, state_of
from app.pipeline import personalization as P

#: The outcomes, in the order the summary prints them.
PUBLISHED = "published"
NOT_LIVE = "published here, still a draft on Etsy"
APPROVED = "approved but not published"
WAITING = "scheduled and waiting"
MISSED = "scheduled and missed"
NOT_APPROVED = "never approved"
REFUSED = "refused by Etsy"
HELD = "held by our own check"
OUTCOMES = (PUBLISHED, NOT_LIVE, APPROVED, WAITING, MISSED, NOT_APPROVED, REFUSED, HELD)
#: Days looked back when no --since is given.
DEFAULT_DAYS = 30
TITLE_CHARS = 40


@dataclass
class Item:
    """One listing in one shop (or with no shop yet)."""

    content: GeneratedContent
    shop: EtsyConnection | None
    sku: str | None
    setting: dict[str, Any] | None
    source: str
    publication: ListingPublication | None = None
    slots: list[PlannedSlot] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    findings: list[tuple[str, str, str]] = field(default_factory=list)  # (rule, severity, blocks)
    etsy_state: str | None = None
    outcome: str = APPROVED
    why: str = ""

    @property
    def personalized(self) -> bool:
        return bool(self.setting and self.setting.get("enabled"))

    def label(self) -> str:
        if self.publication is not None:
            return str(self.publication.etsy_listing_id)
        return f"listing {str(self.content.id)[:8]} (no draft)"


def _when(at: datetime | None) -> str:
    if at is None:
        return "?"
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def report(
    sm: async_sessionmaker,
    email: str,
    since: date | None = None,
    *,
    check_etsy: bool = False,
    client_factory: Callable | None = None,
    token_service: Any = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    since = since or (now - timedelta(days=DEFAULT_DAYS)).date()
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            found = await _gather(session, email, since, now)
            if isinstance(found, str):
                return found
            who, items = found
            session.expunge_all()  # kept as read: the rollback below must not expire them
        finally:
            await session.rollback()
    notes: list[str] = []
    if check_etsy:
        notes = await _check_etsy(sm, items, client_factory=client_factory, token_service=token_service)
    for item in items:
        item.outcome, item.why = classify(item, now)
    return _render(who, since, items, notes, check_etsy)


async def _gather(session: AsyncSession, email: str, since: date, now: datetime):  # noqa: ANN202
    tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
    if tenant is None:
        return f"no account with the address {email}"
    start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    tid = tenant.id
    jobs = list((await session.execute(select(Job).where(
        Job.tenant_id == tid, Job.type.in_((JobType.create_draft, JobType.publish_live)),
        Job.created_at >= start).order_by(Job.created_at))).scalars())
    pubs = list((await session.execute(select(ListingPublication).where(
        ListingPublication.tenant_id == tid, ListingPublication.content_id.is_not(None),
        or_(ListingPublication.created_at >= start, ListingPublication.scheduled_for >= start,
            ListingPublication.published_at >= start)))).scalars())
    slots = list((await session.execute(select(PlannedSlot).where(
        PlannedSlot.tenant_id == tid, PlannedSlot.content_id.is_not(None),
        PlannedSlot.publish_at >= start))).scalars())
    ids = {c for c in (await session.execute(select(GeneratedContent.id).where(
        GeneratedContent.tenant_id == tid, GeneratedContent.created_at >= start))).scalars()}
    ids |= {p.content_id for p in pubs} | {s.content_id for s in slots}
    for job in jobs:
        try:
            ids.add(uuid.UUID(str((job.payload or {}).get("content_id"))))
        except ValueError:
            continue
    contents = {c.id: c for c in (await session.execute(select(GeneratedContent).where(
        GeneratedContent.tenant_id == tid, GeneratedContent.id.in_(ids or {uuid.uuid4()})))).scalars()}
    shops = {s.id: s for s in (await session.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tid))).scalars()}
    profiles = {p.id: p for p in (await session.execute(select(ListingProfile).where(ListingProfile.tenant_id == tid))).scalars()}
    assets = {a.id: a for a in (await session.execute(select(Asset).where(
        Asset.id.in_([c.asset_id for c in contents.values()] or [uuid.uuid4()])))).scalars()}
    findings: dict[uuid.UUID, list[ComplianceFinding]] = defaultdict(list)
    for f in (await session.execute(select(ComplianceFinding).where(
            ComplianceFinding.generated_content_id.in_(list(contents) or [uuid.uuid4()])))).scalars():
        findings[f.generated_content_id].append(f)
    all_pubs = list((await session.execute(select(ListingPublication).where(
        ListingPublication.content_id.in_(list(contents) or [uuid.uuid4()])))).scalars())
    by_pair: dict[tuple[uuid.UUID, uuid.UUID | None], Item] = {}

    def item(content: GeneratedContent, shop_id: uuid.UUID | None) -> Item:
        key = (content.id, shop_id)
        if key not in by_pair:
            profile = profiles.get(content.listing_profile_id) if content.listing_profile_id else None
            setting, source = P.for_listing(content.personalization, profile.personalization if profile else None,
                                            profile.cached_payload if profile else None)
            asset = assets.get(content.asset_id)
            by_pair[key] = Item(content, shops.get(shop_id) if shop_id else None, asset.parsed_sku if asset else None,
                                setting, source, findings=_findings(content, findings.get(content.id, []), setting))
        return by_pair[key]

    for pub in all_pubs:
        if pub.content_id in contents:
            item(contents[pub.content_id], pub.connection_id).publication = pub
    for slot in slots:
        if slot.content_id in contents:
            item(contents[slot.content_id], slot.connection_id).slots.append(slot)
    for job in jobs:
        try:
            cid = uuid.UUID(str((job.payload or {}).get("content_id")))
        except ValueError:
            continue
        if cid in contents:
            item(contents[cid], job.connection_id).jobs.append(job)
    for content in contents.values():
        if not any(k[0] == content.id for k in by_pair):
            item(content, None)
    ordered = sorted(by_pair.values(), key=lambda i: (
        i.shop.shop_name or "" if i.shop else "~", _aware(i.content.created_at), str(i.content.id)))
    return (tenant.email, str(tenant.id)), ordered


def _findings(content: GeneratedContent, rows: list[ComplianceFinding], setting: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """(rule, severity, what it blocks): the stored compliance findings, the title warnings and
    the personalization rules. Names and kinds only, never the seller's words."""
    out = [(f.rule, f.severity.value, "blocks drafting and going live" if f.severity is ComplianceSeverity.blocking
            else "does not block") for f in rows]
    for _ in (content.attributes or {}).get("title_warnings") or []:
        out.append(("title guidance", "warning", "does not block"))
    for problem in P.field_problems(setting):
        if problem.over:
            out.append((f"personalization {problem.field}: {problem.over} characters over {problem.limit}",
                        "rule", "holds new drafts (never sent); not going live of a draft already made"))
        if problem.extra_caps:
            out.append((f"personalization {problem.field}: {problem.extra_caps + 1} words in capitals (Etsy allows one)",
                        "rule", "holds new drafts (never sent); not going live of a draft already made"))
    for problem in P.etsy_problems(setting):
        if "characters" not in problem and "capitals" not in problem:
            out.append((f"personalization: {problem}", "rule", "holds new drafts (never sent)"))
    return out


def _latest(jobs: list[Job], kind: JobType) -> Job | None:
    mine = [j for j in jobs if j.type is kind]
    return mine[-1] if mine else None


def classify(item: Item, now: datetime) -> tuple[str, str]:
    """The listing's outcome in this shop, and the one line that says why."""
    pub, content = item.publication, item.content
    live = _latest(item.jobs, JobType.publish_live)
    draft = _latest(item.jobs, JobType.create_draft)
    if pub is not None and pub.state == "active":
        if item.etsy_state is not None and item.etsy_state != "active":
            return NOT_LIVE, f"the app recorded it live; Etsy says {item.etsy_state}"
        return PUBLISHED, _when(pub.published_at)
    if not content.approved:
        return NOT_APPROVED, "not approved (now)"
    if live is not None and live.status is JobStatus.failed:
        if (live.payload or {}).get("etsy_error") or "Etsy" in (live.last_error or ""):
            return REFUSED, "going live failed with Etsy's answer"
        return HELD, "going live stopped by the app's own check"
    if draft is not None and draft.status is JobStatus.queued and draft.paused_reason == "personalization_held":
        return HELD, "draft held: its personalization breaks Etsy's rules"
    if pub is None and draft is not None and draft.status is JobStatus.failed:
        if (draft.payload or {}).get("etsy_error"):
            return REFUSED, "the draft was refused by Etsy"
        return HELD, "the draft was stopped by the app's own check"
    if pub is not None and pub.schedule_note:
        return HELD, "its scheduled go-live was not released (the reason is below)"
    failed = [s for s in item.slots if s.state == "failed"]
    if failed:
        note = failed[-1].note or ""
        refused = "Etsy" in note and not P.is_held(note)
        return (REFUSED if refused else HELD), "its group schedule failed"
    if pub is None and P.held_reason(item.setting):
        return HELD, "new drafts are held: its personalization breaks Etsy's rules"
    if pub is not None and pub.scheduled_for is not None:
        state = state_of(pub, live if live is not None and pub.schedule_job_id == live.id else None, now)
        if state is not None and state.status == "missed":
            return MISSED, f"due {_when(pub.scheduled_for)}"
        if state is not None and state.status in ("scheduled", "publishing", "waiting"):
            return WAITING, f"{state.status}, due {_when(pub.scheduled_for)}"
    open_slots = [s for s in item.slots if s.state in ("waiting", "drafting")]
    if open_slots:
        due = _aware(open_slots[-1].publish_at)
        if due < now - MISSED_AFTER:
            return MISSED, f"group schedule due {_when(due)}, still {open_slots[-1].state}"
        return WAITING, f"group schedule due {_when(due)}"
    return APPROVED, "approved; no go-live asked for or still to happen" if pub is not None else "approved; no draft yet"


async def _check_etsy(sm: async_sessionmaker, items: list[Item], *, client_factory: Callable | None,
                      token_service: Any) -> list[str]:
    """Read only: each listing with a draft, in its own shop (getListing), through the rate limiter."""
    import httpx

    from app.etsy.api import RateLimitExceeded
    from app.etsy.errors import EtsyClientError
    from app.etsy.guard import EtsyCallsBlocked
    from app.ops.draft_failures import _default_client, _default_tokens

    notes: list[str] = []
    calls = 0
    factory = client_factory or _default_client
    tokens = token_service or _default_tokens()
    by_shop: dict[uuid.UUID, list[Item]] = defaultdict(list)
    for i in items:
        if i.publication is not None and i.publication.state != "deleted_on_etsy":
            by_shop[i.publication.connection_id].append(i)
    async with httpx.AsyncClient(timeout=30.0) as http:
        for shop_id, group in by_shop.items():
            name = (group[0].shop.shop_name if group[0].shop else None) or "a shop"
            try:
                async with sm() as session:  # writable: an expired key is renewed (the OAuth exchange)
                    shop = await session.get(EtsyConnection, shop_id)
                    if shop is None or (shop.access_token_enc is None and shop.refresh_token_enc is None):
                        notes.append(f"{name}: not checked (no Etsy key is stored for this shop; it needs reconnecting)")
                        continue
                    token = await tokens.get_valid_access_token(session, shop)
                    await session.commit()
                client = factory(shop_id, http)
                kw = {"access_token": token, "tenant_id": shop.tenant_id}
                for i in group:
                    calls += 1
                    try:
                        row = await client.get_listing(i.publication.etsy_listing_id, **kw)
                        i.etsy_state = str(row.get("state") or "unknown")
                    except EtsyClientError as exc:
                        if exc.status_code in (404, 410):
                            i.etsy_state = "gone"
                        else:
                            notes.append(f"{name}: listing {i.publication.etsy_listing_id} could not be read "
                                         f"(Etsy answered {exc.status_code})")
            except RateLimitExceeded:
                notes.append("stopped: the app's daily Etsy budget is used up (nothing more was read)")
                break
            except EtsyCallsBlocked:
                notes.append("Etsy calls are blocked here (ETSY_CALLS_BLOCKED): run this on the server")
                break
            except Exception as exc:  # noqa: BLE001 - one shop's trouble does not hide the others
                notes.append(f"{name}: not checked ({type(exc).__name__}: {redact(str(exc))[:160]})")
    notes.insert(0, f"Etsy read: {calls} request(s), read only, through the rate limiter (upkeep)")
    return notes


def _personalization_line(i: Item) -> str:
    s = i.setting
    if not s:
        return f"personalization: not known yet ({i.source})"
    if not s.get("enabled"):
        return f"personalization: off ({i.source})"
    instructions = str(s.get("instructions") or "").strip()
    caps = len(P.shouted(instructions))
    rules = P.etsy_problems(s)
    return (f"personalization: on ({i.source}) · text_input · {'required' if s.get('required') else 'optional'} · "
            f"limit {P.character_limit(s)} · instructions {len(instructions)} characters "
            f"({'within' if len(instructions) <= P.MAX_INSTRUCTIONS else 'over'} {P.MAX_INSTRUCTIONS}), "
            f"{caps} word(s) in capitals · Etsy's rules: " + ("; ".join(rules) if rules else "met"))


def _job_line(job: Job) -> str:
    kind = "draft" if job.type is JobType.create_draft else "go-live"
    status = job.status.value + (f" ({job.paused_reason})" if job.paused_reason else "")
    line = f"    {_when(job.created_at)} {kind:7} {status}"
    if job.finished_at:
        line += f", ended {_when(job.finished_at)}"
    if job.last_error:
        line += f" · ours: {redact(job.last_error)[:200]}"
    etsy = (job.payload or {}).get("etsy_error")
    if isinstance(etsy, dict):
        line += (f" · Etsy {etsy.get('status', '?')} at {etsy.get('step') or etsy.get('path') or '?'}: "
                 f"{redact(str(etsy.get('reason') or etsy.get('message') or ''))[:200]}")
    return line


def _render(who: tuple[str, str], since: date, items: list[Item], notes: list[str], checked: bool) -> str:
    lines = [f"publish report for {who[0]} (account {who[1][:8]}), listings since {since.isoformat()}", ""]
    lines.append("== summary (one line per outcome; personalization on | off)")
    for outcome in OUTCOMES:
        on = [i.label() for i in items if i.outcome == outcome and i.personalized]
        off = [i.label() for i in items if i.outcome == outcome and not i.personalized]
        if not on and not off:
            continue
        lines.append(f"  {outcome}: {len(on) + len(off)} (personalization on {len(on)}, off {len(off)})")
        if on:
            lines.append("    on:  " + ", ".join(on))
        if off:
            lines.append("    off: " + ", ".join(off))
    if not items:
        lines.append("  no listings in this period")
    lines += [""] + ([f"  {n}" for n in notes] if notes else []) + ([""] if notes else [])
    for i in items:
        c = i.content
        title = (c.title or "")[:TITLE_CHARS]
        shop = i.shop.shop_name if i.shop is not None else "no shop yet"
        lines.append(f"== {shop} · {i.label()} · \"{title}\" · SKU {i.sku or '-'}")
        lines.append(f"  outcome: {i.outcome} ({i.why})")
        lines.append("  " + _personalization_line(i))
        if c.approved:
            when = _when(c.approved_at) if c.approved_at else "time not recorded"
            lines.append(f"  approval: approved, {when}, via {c.approved_via or 'not recorded'}")
        else:
            lines.append("  approval: not approved")
        for slot in i.slots:
            lines.append(f"  group schedule: due {_when(slot.publish_at)}, draft from {_when(slot.draft_at)}, state "
                         f"{slot.state}" + (f" · {redact(slot.note)[:200]}" if slot.note else ""))
        if i.publication is not None:
            p = i.publication
            line = f"  draft: listing {p.etsy_listing_id}, state {p.state} here"
            if p.scheduled_for is not None:
                live = next((j for j in i.jobs if j.id == p.schedule_job_id), None)
                state = state_of(p, live)
                line += f" · scheduled {_when(p.scheduled_for)} ({state.status if state else '?'})"
                if p.schedule_note:
                    line += f" · {redact(p.schedule_note)[:200]}"
            lines.append(line)
        if i.jobs:
            lines.append("  attempts:")
            lines += [_job_line(j) for j in i.jobs]
        else:
            lines.append("  attempts: none in this period")
        if i.findings:
            lines.append("  findings: " + "; ".join(f"[{sev}] {rule} ({blocks})" for rule, sev, blocks in i.findings))
        else:
            lines.append("  findings: none")
        if checked:
            lines.append(f"  on Etsy: {i.etsy_state or ('not read' if i.publication is not None else 'no draft')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
