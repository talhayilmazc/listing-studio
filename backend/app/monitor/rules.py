"""What is wrong right now. Each rule reads the database or Redis and says so as problems, grouped per
account; ``notify`` decides what gets said. Read only, and nothing here calls Etsy.

Containers are seen from outside them: deploy/watch.sh leaves the numbers in Redis every minute
under ``ops:containers``.
"""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import mask_email
from app.core.config import get_settings
from app.core.logsafety import redact
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.etsy.scheduling import MISSED_AFTER
from app.monitor import connections, signals
from app.monitor.notify import Problem

DAY = timedelta(days=1)
STUCK_AFTER = timedelta(minutes=30)
ERROR_SHARE = 0.02
ERROR_WINDOW_MINUTES = 10
#: Under this many requests in the window a few errors are not a share of anything.
ERROR_MIN_REQUESTS = 20
BEAT_TOO_OLD_SECONDS = 300
POOL_SHARE = 0.8
DISK_SHARE = 0.85
MEMORY_SHARE = 0.9
#: A container's restart stays a problem this long, then it says "resolved" if it stayed up.
RESTART_SHOWS_SECONDS = 3600
WATCH_TOO_OLD_SECONDS = 300
#: Not waiting by itself: nothing but the seller's fix moves it (the digest lists these).
HELD = ("personalization_held",)

CONTAINERS = "ops:containers"
RESTARTS = "monitor:restarts"
RESTART_EVENTS = "monitor:restart-events"
FIRST_TICK = "monitor:first-tick"
PREFLIGHT_TENANTS = "preflight:tenants"


def _aware(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _short(text: str | None, limit: int = 160) -> str:
    line = " ".join(redact(text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def preflight_key(tenant_id: Any) -> str:
    return f"preflight:{tenant_id}"


async def accounts(session: AsyncSession) -> dict[str, str]:
    """Account id -> its masked email, for every line an operator reads."""
    return {str(i): mask_email(e) for i, e in (await session.execute(select(Tenant.id, Tenant.email))).all()}


async def shop_names(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(select(EtsyConnection.id, EtsyConnection.shop_name, EtsyConnection.display_name))
    return {str(i): (d or s or "a shop") for i, s, d in rows.all()}


async def failed_go_lives(session: AsyncSession, now: datetime) -> dict[str, list[tuple[str, str]]]:
    """Per account: (shop id, why) for each go-live that failed in the last day and has not since
    gone live, and each one our last check refused."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    jobs = list((await session.execute(select(Job).where(
        Job.type == JobType.publish_live, Job.status == JobStatus.failed, Job.finished_at >= now - DAY))).scalars())
    # This runs every minute: only the shops these jobs were for, never every listing that is live.
    live = {(str(c), str(s)) for c, s in (await session.execute(
        select(ListingPublication.content_id, ListingPublication.connection_id).where(
            ListingPublication.state == "active", ListingPublication.connection_id.in_({j.connection_id for j in jobs})))).all()} if jobs else set()
    for job in jobs:
        if (str((job.payload or {}).get("content_id")), str(job.connection_id)) not in live:
            out[str(job.tenant_id)].append((str(job.connection_id), job.last_error or "no reason recorded"))
    notes = await session.execute(select(ListingPublication).where(
        ListingPublication.schedule_note.is_not(None), ListingPublication.scheduled_for >= now - DAY,
        ListingPublication.state != "active", ListingPublication.state != "deleted_on_etsy"))
    for publication in notes.scalars():
        out[str(publication.tenant_id)].append((str(publication.connection_id), publication.schedule_note or ""))
    return out


async def missed_slots(session: AsyncSession, now: datetime) -> dict[str, list[str]]:
    """Per account: the shop of each scheduled go-live or planned draft whose time passed with nothing released."""
    from app.pipeline.schedule_pause import is_paused, paused_shops

    late = now - MISSED_AFTER
    found: list[tuple[str, str]] = []
    publications = await session.execute(select(ListingPublication.tenant_id, ListingPublication.connection_id).where(
        ListingPublication.scheduled_for < late, ListingPublication.schedule_job_id.is_(None),
        ListingPublication.schedule_note.is_(None), ListingPublication.state == "draft"))
    found += [(t, c) for t, c in publications.all()]
    slots = await session.execute(select(PlannedSlot.tenant_id, PlannedSlot.connection_id).where(
        PlannedSlot.state.in_(("waiting", "drafting")), PlannedSlot.publish_at < late))
    found += [(t, c) for t, c in slots.all()]
    if not found:
        return {}
    paused = await paused_shops(session, {t for t, _ in found})
    out: dict[str, list[str]] = defaultdict(list)
    for tenant, shop in found:
        if not is_paused(tenant, shop, paused):  # held on purpose: Resume sends it
            out[str(tenant)].append(str(shop))
    return out


async def stuck_jobs(session: AsyncSession, now: datetime) -> dict[str, list[Job]]:
    """Per account: jobs that should have moved over half an hour ago. A job waiting for a time of
    its own (the day's budget, Etsy down) has that time ahead of it; one held for the seller's fix
    is listed in the digest."""
    late = now - STUCK_AFTER
    out: dict[str, list[Job]] = defaultdict(list)
    rows = await session.execute(select(Job).where(Job.status.in_((JobStatus.queued, JobStatus.running))))
    for job in rows.scalars():
        if job.paused_reason in HELD:
            continue
        since = _aware(job.started_at) if job.status is JobStatus.running else _aware(job.scheduled_at or job.created_at)
        if since is not None and since < late:
            out[str(job.tenant_id)].append(job)
    return out


async def _go_lives(session: AsyncSession, now: datetime, names: dict[str, str]) -> list[Problem]:
    who = await accounts(session)
    out = []
    for tenant, failures in (await failed_go_lives(session, now)).items():
        shops = Counter(shop for shop, _ in failures)
        shop = names.get(shops.most_common(1)[0][0], "a shop")
        n = len(failures)
        out.append(Problem(f"golive_failed:{tenant}", "go-live failed", tenant_id=tenant, line=(
            f"{who.get(tenant, tenant)}: {n} go-live{'' if n == 1 else 's'} failed in the last day, most in {shop}. "
            f"Last reason: {_short(failures[-1][1])}")))
    return out


async def _missed(session: AsyncSession, now: datetime, names: dict[str, str]) -> list[Problem]:
    who = await accounts(session)
    out = []
    for tenant, shops in (await missed_slots(session, now)).items():
        n = len(shops)
        top = names.get(Counter(shops).most_common(1)[0][0], "a shop")
        out.append(Problem(f"missed_slot:{tenant}", "missed slot", tenant_id=tenant, line=(
            f"{who.get(tenant, tenant)}: {n} scheduled listing{'' if n == 1 else 's'} passed {'its' if n == 1 else 'their'} "
            f"time with nothing sent, most in {top}.")))
    return out


async def _stuck(session: AsyncSession, now: datetime, names: dict[str, str]) -> list[Problem]:
    who = await accounts(session)
    out = []
    for tenant, jobs in (await stuck_jobs(session, now)).items():
        types = Counter(j.type.value for j in jobs)
        oldest = min((_aware(j.started_at) if j.status is JobStatus.running else _aware(j.scheduled_at or j.created_at))
                     or now for j in jobs)
        kinds = ", ".join(f"{n} {t}" for t, n in types.most_common())
        out.append(Problem(f"job_stuck:{tenant}", "job stuck", tenant_id=tenant, line=(
            f"{who.get(tenant, tenant)}: {len(jobs)} job{'' if len(jobs) == 1 else 's'} not moving ({kinds}), the oldest for "
            f"{int((now - oldest).total_seconds() // 60)} minutes.")))
    return out


async def _tokens(session: AsyncSession, now: datetime, names: dict[str, str]) -> list[Problem]:
    who = await accounts(session)
    rows = await session.execute(select(EtsyConnection).where(
        (EtsyConnection.status == ConnectionStatus.expired)
        | ((EtsyConnection.status == ConnectionStatus.active) & EtsyConnection.refresh_token_enc.is_(None))))
    lost: dict[str, list[str]] = defaultdict(list)
    for shop in rows.scalars():
        lost[str(shop.tenant_id)].append(names.get(str(shop.id), "a shop"))
    return [Problem(f"token:{tenant}", "shop token stopped working", tenant_id=tenant, line=(
        f"{who.get(tenant, tenant)}: Etsy no longer accepts the sign-in of {len(shops)} shop{'' if len(shops) == 1 else 's'} "
        f"({', '.join(sorted(shops))}). The seller has to connect again; the app says so on Overview and Shops."))
        for tenant, shops in lost.items()]


#: What each reason means (api/auth.py). The code stays in the line: it is what the logs say.
WHY_REFUSED = {
    "no_binding": "the browser that came back holds no cookie from starting the connection",
    "state_mismatch": "the browser that came back started a different connection",
    "other_account": "the browser came back signed in to another account",
    "unavailable": "the account is suspended or being deleted",
    "expired": "it took too long, or the connection was never started here",
    "reused": "the same link was used twice",
    "denied": "the seller said no on Etsy's page",
}


def _why_refused(reason: str) -> str:
    meaning = WHY_REFUSED.get(reason)
    return f"{meaning} ({reason})" if meaning else _short(reason)


async def _connections(session: AsyncSession, redis: Any, now: datetime) -> list[Problem]:
    who = await accounts(session)
    events = await connections.since(redis, now.timestamp() - DAY.total_seconds())
    connected_at: dict[str | None, float] = {}
    for event in events:
        if event.kind == connections.CONNECTED:
            connected_at[event.tenant_id] = event.at
    refused: dict[tuple[str | None, str], int] = Counter()
    attempts: Counter[str] = Counter()
    for event in events:
        if event.at <= connected_at.get(event.tenant_id, 0):
            continue  # that account has connected since: it is fixed
        if event.kind == connections.REFUSED:
            refused[(event.tenant_id, event.reason or "no reason given")] += 1
        elif event.kind == connections.ATTEMPT and event.tenant_id:
            attempts[event.tenant_id] += 1
    out = []
    for (tenant, reason), n in refused.items():
        name = who.get(tenant or "", "an account we could not tell")
        out.append(Problem(f"callback_refused:{tenant or 'unknown'}:{reason}", "shop connection refused", tenant_id=tenant, line=(
            f"{name}: Etsy's return to us was refused {n} time{'' if n == 1 else 's'} in the last day. Reason: {_why_refused(reason)}.")))
    for tenant, n in attempts.items():
        if n >= 2:
            out.append(Problem(f"connect_failing:{tenant}", "shop connection failing", tenant_id=tenant, line=(
                f"{who.get(tenant, tenant)}: {n} tries to connect a shop in the last day and none succeeded.")))
    return out


async def _errors(redis: Any, now: datetime) -> list[Problem]:
    total, errors = await signals.errors_over(redis, ERROR_WINDOW_MINUTES, now.timestamp())
    if total >= ERROR_MIN_REQUESTS and errors / total > ERROR_SHARE:
        return [Problem("api_5xx", "API errors", (
            f"{errors} of {total} requests ({errors / total:.1%}) got a server error in the last {ERROR_WINDOW_MINUTES} minutes."))]
    return []


async def _beats(redis: Any, now: datetime) -> list[Problem]:
    stamp = now.timestamp()
    await redis.set(FIRST_TICK, str(stamp), nx=True, ex=30 * 86400)
    started = float(await redis.get(FIRST_TICK) or stamp)
    out = []
    for key, name, what in ((signals.WORKER_BEAT, "worker", "jobs"), (signals.SCHEDULER_BEAT, "scheduler", "scheduled work")):
        raw = await redis.get(key)
        last = float(raw) if raw else started
        age = stamp - last
        if age > BEAT_TOO_OLD_SECONDS:
            out.append(Problem(f"beat:{name}", f"{name} not running", (
                f"The {name} last reported {int(age // 60)} minutes ago: no {what} are being run.")))
    return out


async def _pool(redis: Any) -> list[Problem]:
    busiest: dict[str, tuple[int, int]] = {}
    for row in await signals.pools(redis):
        if not row["capacity"]:
            continue
        if row["kind"] not in busiest or row["peak"] / row["capacity"] > busiest[row["kind"]][0] / busiest[row["kind"]][1]:
            busiest[row["kind"]] = (row["peak"], row["capacity"])
    return [Problem(f"pool:{kind}", "database connections", f"The {kind} used {peak} of its {capacity} database connections.")
            for kind, (peak, capacity) in busiest.items() if peak / capacity > POOL_SHARE]


async def _idle(redis: Any, now: datetime) -> list[Problem]:
    fired = await signals.idle_fired_over(redis, 60, now.timestamp())
    if not fired:
        return []
    return [Problem("idle_timeout", "idle transaction timeout", (
        f"A database connection was ended with its transaction open {fired} time{'' if fired == 1 else 's'} in the last hour "
        f"(idle_in_transaction_session_timeout, or Postgres restarted). Something held a transaction across slow work."))]


def disk_share(path: str | None = None) -> float | None:
    try:
        usage = shutil.disk_usage(path or get_settings().storage_dir)
    except OSError:
        return None
    return usage.used / usage.total if usage.total else None


def _disk() -> list[Problem]:
    share = disk_share()
    if share is not None and share > DISK_SHARE:
        return [Problem("disk", "disk nearly full", f"The disk is {share:.0%} full.")]
    return []


async def _containers(redis: Any, now: datetime) -> list[Problem]:
    raw = await redis.get(CONTAINERS)
    stamp = now.timestamp()
    if not raw:
        if get_settings().app_env == "production":
            return [Problem("watch", "container watch not running", (
                "deploy/watch.sh has left no numbers: memory, restarts and OOM kills are not being seen."))]
        return []
    data = json.loads(raw)
    out = []
    if stamp - float(data.get("at", 0)) > WATCH_TOO_OLD_SECONDS:
        out.append(Problem("watch", "container watch not running", (
            "deploy/watch.sh has stopped reporting: memory, restarts and OOM kills are not being seen.")))
    day = now.strftime("%Y-%m-%d")
    for c in data.get("containers", []):
        name = str(c.get("name"))
        used, limit = float(c.get("mem_mb") or 0), float(c.get("limit_mb") or 0)
        await _note_peak(redis, day, name, used)
        if limit and used / limit > MEMORY_SHARE:
            out.append(Problem(f"memory:{name}", "container memory", f"{name} is using {used:.0f} MB of its {limit:.0f} MB limit."))
        await _note_restart(redis, name, c, stamp)
    for name, line in (await _restarts(redis, stamp)).items():
        out.append(Problem(f"restart:{name}", "container restarted", line))
    return out


async def _note_peak(redis: Any, day: str, name: str, used: float) -> None:
    key = f"monitor:memory:{day}"
    known = await redis.hget(key, name)
    if known is None or float(known) < used:
        await redis.hset(key, name, f"{used:.0f}")
        await redis.expire(key, 3 * 86400)


async def _note_restart(redis: Any, name: str, container: dict[str, Any], stamp: float) -> None:
    """A restart is the count rising in the same container; a deploy makes a new one, at zero."""
    identity, restarts = str(container.get("id") or ""), int(container.get("restarts") or 0)
    raw = await redis.hget(RESTARTS, name)
    known = json.loads(raw) if raw else None
    await redis.hset(RESTARTS, name, json.dumps({"id": identity, "restarts": restarts}))
    rose = known is not None and known.get("id") == identity and restarts > int(known.get("restarts", 0))
    killed = bool(container.get("oom"))
    if rose or killed:
        what = "was killed for using too much memory (OOM)" if killed else f"restarted ({restarts} times so far)"
        await redis.hset(RESTART_EVENTS, name, json.dumps({"at": stamp, "line": f"{name} {what}."}))


async def _restarts(redis: Any, stamp: float) -> dict[str, str]:
    out = {}
    for field, raw in (await redis.hgetall(RESTART_EVENTS)).items():
        name = field.decode() if isinstance(field, bytes) else field
        event = json.loads(raw)
        if stamp - float(event["at"]) > RESTART_SHOWS_SECONDS:
            await redis.hdel(RESTART_EVENTS, name)
        else:
            out[name] = event["line"]
    return out


async def preflight_results(redis: Any, tenant_id: Any) -> list[dict[str, Any]]:
    """What the check one hour before a run found in this account's shops."""
    out = []
    for raw in (await redis.hgetall(preflight_key(tenant_id))).values():
        out.extend(json.loads(raw).get("problems", []))
    return out


async def _preflight(session: AsyncSession, redis: Any) -> list[Problem]:
    who = await accounts(session)
    out = []
    for raw in await redis.smembers(PREFLIGHT_TENANTS):
        tenant = raw.decode() if isinstance(raw, bytes) else raw
        found = await preflight_results(redis, tenant)
        if not found:
            await redis.srem(PREFLIGHT_TENANTS, tenant)
            continue
        for problem in found:
            out.append(Problem(f"will_fail:{tenant}:{problem['shop']}:{problem['kind']}", "scheduled run will fail", tenant_id=tenant, line=(
                f"{who.get(tenant, tenant)}: {problem['line']} ({problem['count']} scheduled in the next hour.)")))
    return out


async def problems(session: AsyncSession, redis: Any, now: datetime | None = None) -> list[Problem]:
    """Everything wrong right now."""
    now = now or datetime.fromtimestamp(time.time(), timezone.utc)
    names = await shop_names(session)
    found: list[Problem] = []
    found += await _go_lives(session, now, names)
    found += await _missed(session, now, names)
    found += await _stuck(session, now, names)
    found += await _tokens(session, now, names)
    found += await _connections(session, redis, now)
    found += await _errors(redis, now)
    found += await _beats(redis, now)
    found += await _pool(redis)
    found += await _idle(redis, now)
    found += _disk()
    found += await _containers(redis, now)
    found += await _preflight(session, redis)
    return found
