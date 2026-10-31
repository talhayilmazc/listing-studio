"""The daily summary, 09:00 Istanbul: per account what went wrong and what will, then the whole app.
Accounts with nothing to report are counted, not listed. No token, no buyer data, emails masked."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.check import listing_problem
from app.core.config import get_settings
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.monitor import connections, notify, rules, signals
from app.pipeline import shop_listing

ISTANBUL = ZoneInfo("Europe/Istanbul")
DIGEST_HOUR = 9
#: Sent from 09:00 until this hour: a server down all morning does not send yesterday's news at night.
DIGEST_UNTIL_HOUR = 13
SENT = "monitor:digest:{day}"
#: Listings looked at per run for "will fail" (each is a few queries).
MAX_DUE = 3000


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


async def will_fail(session: AsyncSession, redis: Any, now: datetime, until: datetime) -> dict[str, Counter[str]]:
    """Per account: scheduled listings due before ``until`` that would not go, by why. Read from our
    own records (Etsy is only read by the check an hour before a run; what it found is added)."""
    from app.pipeline.schedule_pause import is_paused, paused_shops

    shops = {c.id: c for c in (await session.execute(select(EtsyConnection))).scalars()}
    out: dict[str, Counter[str]] = defaultdict(Counter)
    due: list[tuple[Any, Any, Any]] = []
    rows = await session.execute(
        select(ListingPublication.tenant_id, ListingPublication.connection_id, ListingPublication.content_id)
        .where(ListingPublication.scheduled_for >= now, ListingPublication.scheduled_for < until,
               ListingPublication.schedule_job_id.is_(None), ListingPublication.schedule_note.is_(None),
               ListingPublication.state == "draft").limit(MAX_DUE))
    due += rows.all()
    rows = await session.execute(
        select(PlannedSlot.tenant_id, PlannedSlot.connection_id, PlannedSlot.content_id)
        .where(PlannedSlot.publish_at >= now, PlannedSlot.publish_at < until,
               PlannedSlot.state.in_(("waiting", "drafting", "scheduled"))).limit(MAX_DUE))
    due += rows.all()
    paused = await paused_shops(session, {t for t, _, _ in due}) if due else (set(), set())
    seen = set()
    for tenant, shop_id, content_id in due:
        if (content_id, shop_id) in seen or is_paused(tenant, shop_id, paused):
            continue
        seen.add((content_id, shop_id))
        shop = shops.get(shop_id)
        if shop is None or shop.status is not ConnectionStatus.active:
            out[str(tenant)]["its shop is not connected"] += 1
            continue
        content = await session.get(GeneratedContent, content_id) if content_id else None
        if content is None:
            out[str(tenant)]["its listing is gone"] += 1
        elif not content.approved:
            out[str(tenant)]["it is not approved"] += 1
        elif not shop_listing.approved_in(content, shop_id):
            out[str(tenant)]["its text for that shop changed since it was approved"] += 1
        elif (problem := await listing_problem(session, content, shop_id, scan=False)) is not None:
            out[str(tenant)][rules._short(problem, 80)] += 1
    return out


async def per_account(session: AsyncSession, redis: Any, now: datetime) -> dict[str, list[str]]:
    since = now - timedelta(days=1)
    lines: dict[str, list[str]] = defaultdict(list)

    drafts = await session.execute(select(Job).where(
        Job.type == JobType.create_draft, Job.status == JobStatus.failed, Job.finished_at >= since))
    failed: dict[str, list[Job]] = defaultdict(list)
    for job in drafts.scalars():
        failed[str(job.tenant_id)].append(job)
    for tenant, jobs in failed.items():
        lines[tenant].append(f"failed drafts: {len(jobs)} (last: {rules._short(jobs[-1].last_error, 100)})")

    for tenant, failures in (await rules.failed_go_lives(session, now)).items():
        lines[tenant].append(f"failed go-lives: {len(failures)} (last: {rules._short(failures[-1][1], 100)})")

    held = await session.execute(select(Job).where(Job.status == JobStatus.queued, Job.paused_reason.in_(rules.HELD)))
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for job in held.scalars():
        reasons[str(job.tenant_id)][rules._short(job.last_error, 100) or "held"] += 1
    for tenant, why in reasons.items():
        for reason, n in why.most_common(3):
            lines[tenant].append(f"held: {n}: {reason}")

    for tenant, shops in (await rules.missed_slots(session, now)).items():
        lines[tenant].append(f"missed slots: {len(shops)}")

    for tenant, why in (await will_fail(session, redis, now, now + timedelta(days=1))).items():
        for reason, n in why.most_common(3):
            lines[tenant].append(f"due in 24 hours and will fail: {n}: {reason}")
    for raw in await redis.smembers(rules.PREFLIGHT_TENANTS):
        tenant = raw.decode() if isinstance(raw, bytes) else raw
        for problem in await rules.preflight_results(redis, tenant):
            lines[tenant].append(f"checked on Etsy, will fail: {problem['count']}: {problem['line']}")

    by_seller: Counter[str] = Counter()
    deleted: Counter[str] = Counter()
    rows = await session.execute(select(ListingPublication.tenant_id, func.count()).where(
        ListingPublication.published_by == "seller_on_etsy", ListingPublication.published_at >= since)
        .group_by(ListingPublication.tenant_id))
    for tenant, n in rows.all():
        by_seller[str(tenant)] = n
    rows = await session.execute(select(ListingPublication.tenant_id, func.count()).where(
        ListingPublication.deleted_on_etsy_at >= since).group_by(ListingPublication.tenant_id))
    for tenant, n in rows.all():
        deleted[str(tenant)] = n
    for tenant in set(by_seller) | set(deleted):
        lines[tenant].append(
            f"out of step with Etsy, now followed: {by_seller[tenant]} published in Shop Manager, {deleted[tenant]} deleted there")
    unread = await session.execute(
        select(ListingPublication.tenant_id, func.count()).where(
            ListingPublication.state == "draft", ListingPublication.scheduled_for.is_not(None),
            (ListingPublication.etsy_checked_at.is_(None)) | (ListingPublication.etsy_checked_at < since))
        .group_by(ListingPublication.tenant_id))
    for tenant, n in unread.all():
        lines[str(tenant)].append(f"scheduled drafts not compared with Etsy in the last day: {n}")

    queued = await session.execute(
        select(Job.tenant_id, func.count()).where(Job.status.in_((JobStatus.queued, JobStatus.running))).group_by(Job.tenant_id))
    for tenant, n in queued.all():
        lines[str(tenant)].append(f"queue depth: {n}")
    return lines


async def overall(session: AsyncSession, redis: Any, now: datetime) -> list[str]:
    settings = get_settings()
    since = now - timedelta(days=1)
    events = await connections.since(redis, since.timestamp())
    kinds = Counter(e.kind for e in events)
    reasons = Counter(e.reason or "no reason given" for e in events if e.kind == connections.REFUSED)
    signups = int(await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.created_at >= since)) or 0)
    today, yesterday = now.strftime("%Y-%m-%d"), since.strftime("%Y-%m-%d")
    used_today = int(await redis.get(f"quota:global:{today}") or 0)
    used_yesterday = int(await redis.get(f"quota:global:{yesterday}") or 0)
    limit = settings.global_daily_limit
    out = [
        f"shop connections: {kinds[connections.ATTEMPT]} tried, {kinds[connections.CONNECTED]} succeeded, "
        f"{kinds[connections.REFUSED]} refused" + (
            " (" + "; ".join(f"{n}: {rules._short(r, 60)}" for r, n in reasons.most_common(4)) + ")" if reasons else ""),
        f"sign-ups: {signups}",
        f"Etsy calls: yesterday {used_yesterday:,} of {limit:,}, today so far {used_today:,}",
    ]
    slow = await signals.slowest(redis, [yesterday, today])
    if slow:
        out.append("slowest endpoints (p95): " + "; ".join(f"{r} {ms:,} ms ({n:,})" for r, ms, n in slow))
    else:
        out.append("slowest endpoints: no requests counted")
    peaks = {}
    for day in (yesterday, today):
        for field, value in (await redis.hgetall(f"monitor:memory:{day}")).items():
            name = field.decode() if isinstance(field, bytes) else field
            peaks[name] = max(peaks.get(name, 0.0), float(value))
    if peaks:
        out.append("memory peak: " + ", ".join(f"{n} {v:,.0f} MB" for n, v in sorted(peaks.items())))
    else:
        out.append("memory peak: not measured (deploy/watch.sh has left no numbers)")
    share = rules.disk_share()
    if share is not None:
        out.append(f"disk: {share:.0%} full")
    return out


async def build(session: AsyncSession, redis: Any, now: datetime | None = None, *, take_deferred: bool = False) -> str:
    """The digest's text. ``take_deferred``: it is being sent, so what the hourly cap kept back is said here for good."""
    now = now or datetime.now(timezone.utc)
    who = await rules.accounts(session)
    per = await per_account(session, redis, now)
    local = now.astimezone(ISTANBUL)
    lines = [f"[Listyro] Daily digest, {local:%A %d %B %Y, %H:%M} Istanbul"]
    if per:
        lines.append("")
        lines.append(f"Accounts with something to look at: {len(per)} of {len(who)}")
        for tenant, rows in sorted(per.items(), key=lambda kv: who.get(kv[0], kv[0])):
            lines.append(f"{who.get(tenant, tenant)}")
            lines.extend(f"  - {row}" for row in rows)
    else:
        lines.append(f"All {_plural(len(who), 'account')}: nothing to report.")
    lines.append("")
    lines.append("The whole app")
    lines.extend(f"  - {row}" for row in await overall(session, redis, now))
    opened = await notify.still_open(redis)
    if opened:
        lines.append("")
        lines.append(f"Open problems: {len(opened)}")
        lines.extend(f"  - {p.get('kind')}: {p.get('line')}" for p in opened[:20])
    deferred = await (notify.take_deferred(redis) if take_deferred else _peek_deferred(redis))
    if deferred:
        lines.append("")
        lines.append(f"Kept back by the hourly limit on alerts: {len(deferred)}")
        lines.extend(f"  - {line}" for line in deferred)
    return "\n".join(lines)


async def _peek_deferred(redis: Any) -> list[str]:
    return sorted(v.decode() if isinstance(v, bytes) else v for v in (await redis.hgetall(notify.DEFERRED)).values())


def due(now: datetime) -> str | None:
    """The Istanbul day whose digest may go now, or None."""
    local = now.astimezone(ISTANBUL)
    if DIGEST_HOUR <= local.hour < DIGEST_UNTIL_HOUR:
        return local.strftime("%Y-%m-%d")
    return None
