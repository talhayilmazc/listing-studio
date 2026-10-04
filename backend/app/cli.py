"""Server CLI: grant and withdraw the admin role.

The only way anyone becomes an admin — no HTTP endpoint can set the flag. Run it
on the server, inside the API container:

    docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin you@example.com
    docker compose -f docker-compose.prod.yml exec api python -m app.cli demote-admin someone@example.com
    docker compose -f docker-compose.prod.yml exec api python -m app.cli list-admins
    docker compose -f docker-compose.prod.yml exec api python -m app.cli sales-report seller@example.com

``sales-report`` is for diagnosing Analytics: for each of the account's shops it
prints where the sales read stands and what the daily aggregates hold (counts,
days, totals, and how many listings with sales are in the listing cache). It
reads derived totals only; nothing about buyers exists to print.

``create-admin`` creates a new admin account, or promotes an existing one. The
password is always prompted for, never taken as an argument, so it cannot land
in shell history or a process listing; it must meet the same policy as every
other account. Admins then sign in through the normal login form.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import audit
from app.core.config import get_settings
from app.core.passwords import WeakPassword, hash_password, validate_strength
from app.db.models import Job, JobStatus, ListingPublication, ListingSnapshot, Tenant, TenantStatus

EMAIL_RE = None  # set lazily: importing the API module pulls in the web stack


def _normalise(email: str) -> str:
    global EMAIL_RE
    if EMAIL_RE is None:
        from app.api.accounts import EMAIL_RE as pattern

        EMAIL_RE = pattern
    cleaned = email.strip()
    if not EMAIL_RE.match(cleaned):
        raise SystemExit(f"not a valid email address: {email!r}")
    return cleaned.casefold()


def prompt_password(email: str, attempts: int = 3) -> str:
    """Ask twice, check the policy, never echo. Refuses non-interactive input."""
    if not sys.stdin.isatty():
        raise SystemExit(
            "run this interactively (docker compose exec, without -T): the password is "
            "typed at a prompt, never piped or passed as an argument"
        )
    for _ in range(attempts):
        first = getpass.getpass("New admin password (12+ characters): ")
        try:
            validate_strength(first, email=email)
        except WeakPassword as exc:
            print(f"  {exc}")
            continue
        if getpass.getpass("Repeat it: ") != first:
            print("  those did not match")
            continue
        return first
    raise SystemExit("no password set")


async def create_admin(
    sm: async_sessionmaker,
    email: str,
    *,
    ask_password: Callable[[str], str],
    set_password: bool = False,
) -> str:
    """Create a new admin, or promote an existing account. Returns what happened."""
    email = _normalise(email)
    async with sm() as session:
        tenant = (
            await session.execute(select(Tenant).where(func.lower(Tenant.email) == email))
        ).scalar_one_or_none()

        if tenant is not None:
            changed = []
            if not tenant.is_admin:
                tenant.is_admin = True
                audit.record(session, "admin.promoted", actor=None, target_tenant_id=tenant.id)
                changed.append("promoted to admin")
            if set_password:
                password = ask_password(email)
                validate_strength(password, email=email)
                tenant.password_hash = hash_password(password)
                tenant.must_change_password = False
                audit.record(session, "admin.password_set", actor=None, target_tenant_id=tenant.id)
                changed.append("password set")
            await session.commit()
            return f"{email}: " + (", ".join(changed) if changed else "already an admin; nothing changed")

        password = ask_password(email)
        validate_strength(password, email=email)  # again: never trust the prompt alone
        tenant = Tenant(
            email=email,
            password_hash=hash_password(password),
            status=TenantStatus.active,
            is_admin=True,
        )
        session.add(tenant)
        await session.flush()
        audit.record(session, "admin.created", actor=None, target_tenant_id=tenant.id)
        await session.commit()
        return f"{email}: admin account created; sign in through the normal login page"


async def demote_admin(sm: async_sessionmaker, email: str) -> str:
    email = _normalise(email)
    async with sm() as session:
        tenant = (
            await session.execute(select(Tenant).where(func.lower(Tenant.email) == email))
        ).scalar_one_or_none()
        if tenant is None or not tenant.is_admin:
            return f"{email}: not an admin; nothing changed"
        admins = await session.scalar(
            select(func.count()).select_from(Tenant).where(Tenant.is_admin)
        )
        if admins <= 1:
            raise SystemExit(f"{email} is the last admin; create another before removing this one")
        tenant.is_admin = False
        audit.record(session, "admin.demoted", actor=None, target_tenant_id=tenant.id)
        await session.commit()
        return f"{email}: no longer an admin"


async def sales_report(sm: async_sessionmaker, email: str) -> str:
    """Where one account's sales data stands, per shop (for the operator)."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import EtsyConnection, SalesDaily, SalesSync, ShopListingCache

    lines: list[str] = []
    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == _normalise(email)))).scalars().first()
        if tenant is None:
            return f"no account {email}"
        shops = (await session.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id))).scalars().all()
        if not shops:
            return f"{email}: no shops"
        today = datetime.now(timezone.utc).date()
        for c in shops:
            lines.append(f"== {c.shop_name or c.shop_id} ({c.status.value}, connection {c.id})")
            lines.append(f"   sales permission: {'yes' if 'transactions_r' in (c.scopes or []) else 'NO (reconnect)'}")
            sync = await session.get(SalesSync, c.id)
            if sync is None:
                lines.append("   sales read: never started (the seller must estimate and start it on Analytics)")
            else:
                lines.append(
                    f"   sales read: {sync.state}; {sync.read_count} of {sync.window_count} sales, "
                    f"{sync.requests_used} requests; started {sync.started_at}, finished {sync.finished_at}"
                    + (f"; note: {sync.note}" if sync.note else "")
                )
            where = SalesDaily.connection_id == c.id
            rows, first, last = (await session.execute(
                select(func.count(), func.min(SalesDaily.day), func.max(SalesDaily.day)).where(where)
            )).one()
            listings = await session.scalar(select(func.count(func.distinct(SalesDaily.listing_id))).where(where))
            lines.append(f"   sales_daily: {rows} rows, {listings} listings, days {first} .. {last}")
            for currency, units, revenue in (await session.execute(
                select(SalesDaily.currency, func.sum(SalesDaily.units), func.sum(SalesDaily.revenue_minor))
                .where(where).group_by(SalesDaily.currency)
            )).all():
                lines.append(f"     {currency or '(no currency)'}: {units} units, revenue {int(revenue or 0) / 100:,.2f}")
            recent = await session.scalar(
                select(func.sum(SalesDaily.revenue_minor)).where(where, SalesDaily.day > today - timedelta(days=30))
            )
            zero = await session.scalar(select(func.count()).select_from(SalesDaily).where(where, SalesDaily.revenue_minor == 0))
            lines.append(f"     last 30 days revenue {int(recent or 0) / 100:,.2f}; rows with zero revenue: {zero}")
            cached = await session.scalar(select(func.count()).select_from(ShopListingCache).where(ShopListingCache.connection_id == c.id))
            with_sales = set((await session.execute(select(func.distinct(SalesDaily.listing_id)).where(where))).scalars())
            in_cache = set((await session.execute(
                select(ShopListingCache.listing_id).where(ShopListingCache.connection_id == c.id)
            )).scalars())
            lines.append(
                f"   listing cache: {cached} listings; Etsy counts {c.listing_counts or 'not recorded yet'}; "
                f"listings with sales found in the cache: {len(with_sales & in_cache)} of {len(with_sales)}"
            )
    return "\n".join(lines)


async def limits_report(sm: async_sessionmaker, email: str) -> str:
    """One account's three numbers as every screen shows them, and every change
    an admin (or a migration) made to them, with its time: when two screens seem
    to disagree, this says what the stored values are and when they changed."""
    from redis.asyncio import Redis

    from app.core import allowance, limits
    from app.db.models import AuditLog
    from app.etsy.rate_limiter import DailyQuota

    settings = get_settings()
    quota = DailyQuota(Redis.from_url(settings.redis_url), global_daily_limit=settings.global_daily_limit,
                       pause_percent=settings.global_pause_percent)
    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
        if tenant is None:
            return f"no account with the address {email}"
        a = await allowance.status(session, tenant)
        c = await limits.etsy_ceiling(quota, tenant)
        app = await limits.app_budget(quota)
        changes = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.target_tenant_id == tenant.id, AuditLog.action.in_(("user.quota_changed", "user.allowance_changed")))
                .order_by(AuditLog.created_at.desc())
                .limit(12)
            )
        ).scalars().all()
    lines = [
        f"account: {tenant.email}   time zone: {limits.zone_of(tenant)}",
        f"1. Listings generated: {a.used} used of {a.amount} ({a.period}, {'its own' if a.custom else 'the default'}); "
        f"{a.remaining} left; resets {allowance.reset_label(a.resets_at, a.time_zone)}",
        f"   stored: allowance_amount={tenant.allowance_amount!r} allowance_period={tenant.allowance_period!r}",
        f"2. Etsy requests today: {c.used} used of {c.limit} ({'follows the default' if c.follows_default else 'its own number'}); "
        f"{c.remaining} left; upkeep not counted: {c.upkeep}; resets {c.resets_label} (00:00 UTC)",
        f"   stored: etsy_ceiling_override={tenant.etsy_ceiling_override!r}; the default is {c.default}",
        f"3. The app's Etsy budget: {app.used} used of {app.limit}; new work pauses at {app.pause_at} ({app.remaining} to go)",
        "changes to this account's limits, newest first:" if changes else "no recorded changes to this account's limits",
    ]
    for row in changes:
        what = "Etsy ceiling" if row.action == "user.quota_changed" else "allowance"
        lines.append(f"   {row.created_at:%Y-%m-%d %H:%M} UTC  {what}: {row.details.get('previous')!r} -> {row.details.get('new')!r}"
                     + (f"  ({row.details['by']})" if row.details.get("by") else ""))
    return "\n".join(lines)


async def upload_retention_report(sm: async_sessionmaker, *, apply: bool) -> str:
    """What upload retention would delete now, or (with ``apply``) delete it.

    The daily job does the same by itself (workers/upkeep.py); this is for
    looking before the first run, or for freeing space without waiting.
    """
    from app.api.deps import get_storage
    from app.core.disk import size
    from app.pipeline import upload_retention

    async with sm() as session:
        result = await upload_retention.run(session, get_storage(), apply=apply)
        if apply:
            await upload_retention.remember(session, result)
    lines = [
        f"kept {result.published_days} days after publishing, {result.unpublished_days} days when nothing was published",
        f"listings published {result.published_days}+ days ago:      {result.published_groups}",
        f"groups not published after {result.unpublished_days} days:    {result.unpublished_groups}",
        f"images: {result.images}   files (with previews): {result.files}   size: {size(result.freed_bytes)}",
        f"waiting for running work to finish: {result.waiting}",
    ]
    if apply:
        lines.append(f"deleted. Cover thumbnails kept: {result.thumbnails} ({size(result.thumbnail_bytes)})")
    else:
        lines.append("nothing was deleted: run again with --apply to delete these files")
    return "\n".join(lines)


async def rebuild_publications(sm: async_sessionmaker, *, apply: bool) -> str:
    """Put back publication records that batch deletion removed.

    Until migration 0036 deleting a batch deleted its publication rows. Two
    things it did not delete say what they were: the snapshot taken when each
    draft was created (the Etsy listing id, the title that was sent; kept 90
    days) and the job rows (which shop, and whether a publish-live for that
    listing succeeded and when). From those, each missing record is rebuilt
    without its content. Nothing is read from Etsy.

    What cannot be rebuilt: drafts created more than 90 days ago (their
    snapshots are gone), and the SKU. A listing published by hand in Shop
    Manager rather than through the app is rebuilt as a draft.
    """
    made = 0
    seen = 0
    async with sm() as session:
        known = {
            (c, l) for c, l in (await session.execute(
                select(ListingPublication.connection_id, ListingPublication.etsy_listing_id))).all()
        }
        live: dict[tuple[uuid.UUID, int], datetime] = {}
        rows = (await session.execute(
            select(ListingSnapshot, Job).join(Job, Job.id == ListingSnapshot.job_id).order_by(ListingSnapshot.taken_at)
        )).all()
        for snapshot, job in rows:
            if (snapshot.payload or {}).get("operation") == "publish_live" and job.status is JobStatus.succeeded:
                live[(job.connection_id, snapshot.listing_id)] = job.finished_at or snapshot.taken_at
        for snapshot, job in rows:
            payload = snapshot.payload or {}
            if payload.get("operation") != "create_draft":
                continue
            seen += 1
            key = (job.connection_id, snapshot.listing_id)
            if key in known:
                continue
            known.add(key)
            went_live = live.get(key)
            made += 1
            if apply:
                session.add(ListingPublication(
                    tenant_id=snapshot.tenant_id, content_id=None, connection_id=job.connection_id,
                    etsy_listing_id=snapshot.listing_id, state="active" if went_live else "draft",
                    title=(payload.get("submitted") or {}).get("title"), published_at=went_live,
                    created_at=snapshot.taken_at, manual_done={},
                ))
        if apply:
            audit.record(session, "publications.rebuilt", actor=None, rebuilt=made)
            await session.commit()
    verb = "rebuilt" if apply else "would rebuild"
    return (f"{seen} draft snapshots found; {verb} {made} missing publication record(s)."
            + ("" if apply else " Run again with --apply to write them."))


async def list_admins(sm: async_sessionmaker) -> list[str]:
    async with sm() as session:
        rows = (
            await session.execute(select(Tenant).where(Tenant.is_admin).order_by(Tenant.email))
        ).scalars()
        return [f"{t.email}  ({t.status.value})" for t in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="create a new admin, or promote an existing account")
    create.add_argument("email")
    create.add_argument(
        "--set-password",
        action="store_true",
        help="when promoting, also set a new password (prompted)",
    )
    demote = sub.add_parser("demote-admin", help="withdraw the admin role (never the last one)")
    demote.add_argument("email")
    sub.add_parser("list-admins", help="list admin accounts")
    rebuild = sub.add_parser(
        "rebuild-publications",
        help="restore publication records lost to batch deletion, from the draft snapshots (last 90 days)",
    )
    rebuild.add_argument("--apply", action="store_true", help="write the records (default: only count them)")
    uploads = sub.add_parser(
        "upload-retention",
        help="what upload retention would delete now (image files past their time); --apply deletes them",
    )
    uploads.add_argument("--apply", action="store_true", help="delete the files (default: only count them)")
    limits_cmd = sub.add_parser(
        "limits-report",
        help="an account's listing allowance, Etsy ceiling and the app's budget as every screen shows them, and their changes",
    )
    limits_cmd.add_argument("email")
    report = sub.add_parser("sales-report", help="where an account's sales data stands (Analytics diagnosis)")
    report.add_argument("email")
    args = parser.parse_args(argv)

    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    if args.command == "create-admin":
        print(asyncio.run(create_admin(sm, args.email, ask_password=prompt_password,
                                       set_password=args.set_password)))
    elif args.command == "demote-admin":
        print(asyncio.run(demote_admin(sm, args.email)))
    elif args.command == "sales-report":
        print(asyncio.run(sales_report(sm, args.email)))
    elif args.command == "limits-report":
        print(asyncio.run(limits_report(sm, args.email)))
    elif args.command == "upload-retention":
        print(asyncio.run(upload_retention_report(sm, apply=args.apply)))
    elif args.command == "rebuild-publications":
        print(asyncio.run(rebuild_publications(sm, apply=args.apply)))
    else:
        admins = asyncio.run(list_admins(sm))
        print("\n".join(admins) if admins else "no admins yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
