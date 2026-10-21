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


async def storage_report(sm: async_sessionmaker) -> str:
    """Stored image files per account (id prefix) and kind: counts, sizes, ages, and
    whether their listing group still needs them. No file names, no personal data."""
    from app.pipeline import storage_report as sr
    from app.pipeline.storage import LocalStorage

    async with sm() as session:
        report = await sr.build(session, LocalStorage(get_settings().storage_dir))
    return sr.render(report)


async def storage_originals(sm: async_sessionmaker, *, apply: bool, storage=None) -> str:  # noqa: ANN001
    """Delete the original uploads stored before only processed copies were kept.
    Dry run by default."""
    from app.api.deps import get_storage
    from app.core.disk import size
    from app.pipeline import originals

    async with sm() as session:
        result = await originals.run(session, storage or get_storage(), apply=apply)
    verb = "deleted" if apply else "would delete (dry run: nothing deleted; run again with --apply)"
    lines = [
        f"originals {verb}: {result.assets:,} images, {result.files:,} files, {size(result.freed_bytes)}",
        f"keys cleared with no file left: {result.cleared_only:,}",
    ]
    if result.kept_no_processed:
        lines.append(f"kept, because their processed copy is missing: {result.kept_no_processed:,}")
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
        f"kept {result.drafted_days} days after drafts exist in every target shop, "
        f"{result.unpublished_days} days after the last work otherwise",
        f"drafted everywhere {result.drafted_days}+ days ago:   {result.drafted_groups}",
        f"not worked on for {result.unpublished_days}+ days:      {result.unpublished_groups}",
        f"images: {result.images}   files (with previews): {result.files}   size: {size(result.freed_bytes)}",
        f"waiting for something pending (schedule, distribution, draft or replace): {result.waiting}",
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


def oauth_check() -> str:
    """What the app sends Etsy to connect a shop, and what is wrong with it.
    No secret is printed (the keystring is public: it is in every authorize URL)."""
    from urllib.parse import parse_qs, urlparse

    from app.core.config import get_settings
    from app.etsy.oauth import build_authorize_url, clean_scopes

    settings = get_settings()
    lines = ["Etsy connect: what the authorize URL carries"]
    problems: list[str] = []
    try:
        scopes = clean_scopes(settings.etsy_scopes)
        if len(scopes) != len(settings.etsy_scopes.split()):
            problems.append("ETSY_SCOPES repeats a scope (it is sent once)")
    except ValueError as exc:
        return f"FAIL: {exc}"
    url = build_authorize_url(authorize_url=settings.etsy_oauth_authorize_url, client_id=settings.etsy_client_id,
                              redirect_uri=settings.etsy_redirect_uri, scopes=settings.etsy_scopes,
                              state="STATE", verifier="V" * 64)
    query = parse_qs(urlparse(url).query)
    lines += [
        f"  authorize: {settings.etsy_oauth_authorize_url}",
        f"  client_id: {'set' if settings.etsy_client_id else 'MISSING'}",
        f"  redirect_uri: {settings.etsy_redirect_uri}",
        f"  scopes: {' '.join(scopes)} (sent as {url.split('scope=')[1].split('&')[0]})",
        f"  code_challenge_method: {query['code_challenge_method'][0]}",
    ]
    redirect = urlparse(settings.etsy_redirect_uri)
    if not settings.etsy_client_id:
        problems.append("ETSY_CLIENT_ID is not set")
    if redirect.path != "/api/auth/etsy/callback":
        problems.append("ETSY_REDIRECT_URI does not end in /api/auth/etsy/callback")
    if redirect.scheme != "https" and redirect.hostname not in ("localhost", "127.0.0.1"):
        problems.append("ETSY_REDIRECT_URI is not https")
    if settings.etsy_redirect_uri.endswith("/"):
        problems.append("ETSY_REDIRECT_URI ends with '/': Etsy compares it character by character")
    lines.append("  Etsy refuses (HTTP 400 on its own page) unless redirect_uri is EXACTLY one of the app's")
    lines.append("  callback URLs at etsy.com/developers/your-apps, and every scope is allowed for the app.")
    lines += [f"PROBLEM: {p}" for p in problems] or ["no problem found in the configuration"]
    return "\n".join(lines)


async def draft_cost(sm: async_sessionmaker) -> str:
    """Requests per draft: the measured average, the estimate plans use, the worst case."""
    from app.core import request_cost

    async with sm() as session:
        e = await request_cost.measure(session)
    mean = f"{e.measured_mean:.1f}" if e.measured_mean is not None else "none yet"
    lines = [
        f"window: last {request_cost.WINDOW_DAYS} complete UTC days" + (f" (data since {e.since})" if e.since else " (no data)"),
        f"draft requests: {e.requests:,}   drafts finished: {e.drafts:,}   measured average: {mean}",
        f"estimate used: {e.per_draft} per draft ({e.source}"
        + (f", average + {int(e.margin * 100)}%" if e.source == "measured" else f", fewer than {request_cost.MIN_DRAFTS} drafts measured")
        + ")",
        f"worst case: {e.worst_case} per draft (gate.JOB_COST; no longer used to admit drafts)",
        f"240 drafts (a 16-shop day): {240 * e.per_draft:,} estimated, {240 * e.worst_case:,} at the worst case",
    ]
    return "\n".join(lines)


def _client_for_check(shop_id: uuid.UUID, http):  # noqa: ANN001, ANN202
    """An Etsy client paced by the app's own bucket and counted in its budget (upkeep)."""
    from redis.asyncio import Redis

    from app.etsy.api import EtsyApiClient
    from app.etsy.rate_limiter import DailyQuota, TokenBucket

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    return EtsyApiClient(
        client_id=settings.etsy_client_id, shared_secret=settings.etsy_client_secret, http_client=http,
        bucket=TokenBucket(redis, rate=settings.etsy_requests_per_second),
        quota=DailyQuota(redis, global_daily_limit=settings.global_daily_limit, pause_percent=settings.global_pause_percent),
        shop=shop_id, upkeep=True,
    )


def _token_service():  # noqa: ANN202
    from app.core.crypto import get_cipher
    from app.etsy.connection import ConnectionService

    settings = get_settings()
    return ConnectionService(get_cipher(), client_id=settings.etsy_client_id, token_url=settings.etsy_oauth_token_url)


async def listing_stats_check(
    sm: async_sessionmaker, target: str, *, client_factory: Callable | None = None, token_service=None,  # noqa: ANN001
) -> str:
    """Does Etsy's listing data carry ``views`` and ``num_favorers`` for this shop?

    Exactly ONE read-only Etsy request: getListingsByListingIds for up to 100 of
    the listings the app published there (what the daily read uses), or, when it
    published none, getListingsByShop for up to 100 active listings (what the
    shop sync reads). Prints counts only: no title, no listing content, no
    buyer data. ``target``: the shop's id in the app, its Etsy shop id, or the
    account's e-mail (when the account has one connected shop).
    """
    import httpx

    from app.db.models import ConnectionStatus, EtsyConnection

    async with sm() as session:
        shops: list[EtsyConnection] = []
        try:
            found = await session.get(EtsyConnection, uuid.UUID(target))
            shops = [found] if found is not None else []
        except ValueError:
            if target.isdigit():
                shops = list((await session.execute(
                    select(EtsyConnection).where(EtsyConnection.shop_id == int(target)))).scalars())
            else:
                tenant = (await session.execute(
                    select(Tenant).where(func.lower(Tenant.email) == target.strip().lower()))).scalar_one_or_none()
                if tenant is not None:
                    shops = list((await session.execute(
                        select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id))).scalars())
        shops = [c for c in shops if c.status is ConnectionStatus.active]
        if not shops:
            return f"no connected shop found for {target!r}; no request made"
        if len(shops) > 1:
            listed = "\n".join(f"  {c.id}  {c.shop_name or c.shop_id}" for c in shops)
            return f"{target!r} has {len(shops)} connected shops; run again with one shop id (no request made):\n{listed}"
        shop = shops[0]
        if shop.shop_id is None:
            return "this shop's Etsy shop id is not known yet (open the app once); no request made"
        ids = list((await session.execute(
            select(ListingPublication.etsy_listing_id).where(
                ListingPublication.connection_id == shop.id, ListingPublication.published_at.is_not(None),
                ListingPublication.state != "deleted_on_etsy",
            ).order_by(ListingPublication.published_at.desc()).limit(100)
        )).scalars())
        token = await (token_service or _token_service()).get_valid_access_token(session, shop)
        kw = {"access_token": token, "tenant_id": shop.tenant_id}
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = (client_factory or _client_for_check)(shop.id, http)
            if ids:
                endpoint = f"getListingsByListingIds ({len(ids)} listing(s) published with the app)"
                resp = await client.get_listings_by_listing_ids(ids, **kw)
            else:
                endpoint = "getListingsByShop (state=active, limit=100; the app has published none here)"
                resp = await client.get_listings_by_shop(shop.shop_id, state="active", limit=100, **kw)
    results = [r for r in resp.get("results") or [] if isinstance(r, dict)]

    def counts(field: str) -> str:
        has = sum(1 for r in results if field in r)
        numbers = sum(1 for r in results if isinstance(r.get(field), int))
        return f"{field}: present in {has} of {len(results)}, a number in {numbers}, missing in {len(results) - has}"

    lines = [
        f"shop: {shop.id} (Etsy shop {shop.shop_id})",
        "Etsy requests made: 1",
        f"endpoint: {endpoint}",
        f"listings returned: {len(results)}",
        counts("views"),
        counts("num_favorers"),
    ]
    if results and not any("views" in r for r in results):
        lines.append("Etsy did not return views: the daily read will store them as unknown, never as zero.")
    return "\n".join(lines)


def _check_design() -> bytes:
    """A small made-up design to show the model: never a seller's."""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 800), (245, 240, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 170, 620, 690), fill=(60, 110, 90))  # the garment
    draw.ellipse((300, 280, 500, 480), fill=(240, 200, 60))  # a sun
    for n, word in enumerate(("PLANT", "LADY")):
        draw.text((330, 520 + 50 * n), word, fill=(250, 250, 245))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


async def llm_check(models: list[str] | None = None) -> tuple[str, bool]:
    """One real vision call and one real content call per model, as listings make
    them (same prompts, schema and thinking setting), on a made-up design.

    Proves the provider accepts what this app sends each model: a refused
    setting is a 400 on every listing. Returns the report and whether every
    call was accepted as sent. Costs a few cents; the calls are metered as our
    own (``eval``), with no seller. Never prints the key.
    """
    import time

    from app.core import ai_meter, ai_prices
    from app.pipeline import llm
    from app.pipeline.content import AnthropicContentGenerator, ContentValidationError, policy_for
    from app.pipeline.templates import load_template
    from app.pipeline.vision import AnthropicVisionAnalyzer

    settings = get_settings()
    if not settings.llm_api_key:
        return "LLM_API_KEY is not set: nothing was called.", False
    wanted = models or list(dict.fromkeys(
        m for m in (settings.vision_model or settings.llm_model, settings.content_model or settings.llm_model) if m))
    design = _check_design()
    table = ai_prices.merged(None)
    lines = [f"LLM_THINKING={settings.llm_thinking}" + (f", LLM_EFFORT={settings.llm_effort}" if settings.llm_effort else "")]
    fine, total = True, 0.0

    def priced(usage) -> float:  # noqa: ANN001
        value = ai_prices.cost(
            usage.model, table, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens - usage.cache_creation_1h_input_tokens,
            cache_write_1h_tokens=usage.cache_creation_1h_input_tokens, cache_read_tokens=usage.cache_read_input_tokens,
        )
        return float(value or 0)

    for model in wanted:
        client = llm.AnthropicLLMClient(
            api_key=settings.llm_api_key, model=model, thinking=settings.llm_thinking, effort=settings.llm_effort)
        sent = llm.thinking_params(model, settings.llm_thinking, settings.llm_effort)
        listed = "" if model in llm.SUPPORTED_MODELS else "  (not one of the supported models)"
        lines.append(f"{model}{listed}: thinking setting sent: {sent.get('thinking') or 'none'}"
                     + (f", effort {sent['effort']}" if sent.get("effort") else ""))
        analysis = None
        with ai_meter.origin("eval"):
            started = time.monotonic()
            try:
                vision = await AnthropicVisionAnalyzer(client).analyze(design, "image/jpeg")
                analysis = vision.analysis
                spent = priced(vision.usage)
                total += spent
                lines.append(f"  vision   ok   {time.monotonic() - started:5.1f} s  in {vision.usage.input_tokens + vision.usage.cache_read_input_tokens + vision.usage.cache_creation_input_tokens:,} / out {vision.usage.output_tokens:,}  ${spent:.4f}")
            except Exception as exc:  # noqa: BLE001 - the report is the point
                fine = False
                lines.append(f"  vision   FAILED  {type(exc).__name__} (status {getattr(exc, 'status_code', None)}): {str(exc)[:200]}")
            if analysis is not None:
                started = time.monotonic()
                generator = AnthropicContentGenerator(client, template=load_template("content/apparel"), policy=policy_for("apparel"))
                try:
                    result = await generator.generate(analysis, None)
                    usages, note = result.usages, f"{result.attempts} attempt(s)"
                except ContentValidationError as exc:
                    # The provider answered; our own checks sent the text back twice. Not a refused request.
                    usages, note = exc.usages, "answered; the text failed our checks twice"
                except Exception as exc:  # noqa: BLE001
                    fine, usages, note = False, [], ""
                    lines.append(f"  content  FAILED  {type(exc).__name__} (status {getattr(exc, 'status_code', None)}): {str(exc)[:200]}")
                if usages:
                    spent = sum(priced(u) for u in usages)
                    total += spent
                    lines.append(f"  content  ok   {time.monotonic() - started:5.1f} s  in {sum(u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens for u in usages):,} / out {sum(u.output_tokens for u in usages):,}  ${spent:.4f}  ({note})")
        if model in llm._REFUSED:  # noqa: SLF001
            fine = False
            lines.append("  REFUSED: the provider answered 400 to this thinking setting; the calls above went through without one.")
    lines.append(f"Total: ${total:.4f}. " + ("Every call was accepted as sent." if fine else "NOT every call was accepted as sent: see above."))
    return "\n".join(lines), fine


async def purge_etsy_data(sm: async_sessionmaker, *, apply: bool) -> str:
    """Delete all stored Etsy data, for every account (docs/etsy-access-ended.md).
    A dry run unless ``apply``."""
    from app.ops import etsy_data

    async with sm() as session:
        result = await etsy_data.wipe(session, apply=apply)
    return etsy_data.report(result, what="All stored Etsy data, every account")


async def scrub_etsy_data(sm: async_sessionmaker, *, apply: bool) -> str:
    """The same wipe on a non-production database, with shop names made anonymous.
    Refuses to run when APP_ENV=production."""
    from app.ops import etsy_data

    etsy_data.refuse_in_production()
    async with sm() as session:
        result = await etsy_data.wipe(session, apply=apply, anonymise_shops=True, action="etsy_data.scrubbed")
    return etsy_data.report(result, what="Etsy-derived data in this non-production database (shop names made anonymous)")


#: The demo data's own addresses: everything ``seed-demo --remove`` deletes is found by them.
DEMO_ACCOUNTS = "demo-%@example.test"
DEMO_REQUESTS = "%@example.test"


async def seed_demo(sm: async_sessionmaker, *, admin: bool = False, shops: int | None = None, groups: int | None = None) -> str:
    """Fill a non-production database with one synthetic demo account: nine shops in two groups,
    profiles, a batch of drawn sample designs, listings, drafts and sales figures.
    Nothing in it is a real seller's. Refuses to run when APP_ENV=production.

    ``shops`` / ``groups``: how many shops and shop groups (default nine in two); the layout
    runs use 1, 4 and 15 shops with 6 groups.

    ``admin`` makes the demo account an admin and adds what the Admin screens need
    to be laid out against: invite requests whose e-mail address, shop link and
    note are long and unbroken (the layout test, frontend/e2e/layout.spec.ts).
    """
    from pathlib import Path

    from app.core import terms
    from app.core.passwords import generate_temp_password, hash_password
    from app.ops import etsy_data

    etsy_data.refuse_in_production()
    script = Path(__file__).resolve().parent.parent / "scripts" / "demo_screens_seed.py"
    if not script.is_file():
        return f"the seed script is not in this image ({script}); run it from a development checkout"
    email = "demo-shots@example.test"
    async with sm() as session:
        if await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.email == email)):
            return f"{email} already exists; delete it first (docs/marketing-screens.md) or sign in to it"
    # The script makes the account and its sample data. In a process of its own:
    # it runs its own event loop, and a database connection belongs to one loop.
    import os
    import subprocess

    root = str(script.parent.parent)  # so the script can import ``app``
    done = await asyncio.to_thread(
        subprocess.run, [sys.executable, str(script)], capture_output=True, text=True, cwd=root,
        env={**os.environ, "PYTHONPATH": root,
             **({"DEMO_SHOPS": str(shops)} if shops else {}), **({"DEMO_GROUPS": str(groups)} if groups is not None else {})},
    )
    if done.returncode != 0:
        return "the seed script failed:\n" + (done.stderr or done.stdout)[-2000:]
    password = generate_temp_password()
    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == email))).scalar_one()
        tenant.password_hash = hash_password(password)
        terms.accept(tenant)
        if admin:
            from app.db.models import InviteRequest

            tenant.is_admin = True
            long_address = "a-very-long-address-that-keeps-going-" + "and-going-" * 6 + "to-the-end@example.test"
            session.add_all([
                InviteRequest(email="maker@example.test", shop="Northwind Tees", shop_count=1, note="Print-on-demand tees, about ten new designs a week."),
                InviteRequest(
                    email=long_address,
                    shop="https://www.example.test/shop/" + "AnUnbrokenShopNameThatIsFarTooLongForOneLine" * 3,
                    shop_count=16,
                    note="unbroken" * 40,
                ),
            ])
        await session.commit()
    lines = [
        "Synthetic demo account ready" + (" (an admin, with sample invite requests)." if admin else "."),
        f"  e-mail:   {email}",
        f"  password: {password}  (shown once; made up for this database)",
        "Everything in it is sample data drawn by the app's own scripts.",
        "Remove it again with: python -m app.cli seed-demo --remove",
    ]
    return chr(10).join(lines)


async def remove_demo(sm: async_sessionmaker, storage: Any = None) -> str:
    """Take the synthetic demo data out again: the demo accounts with everything of
    theirs, their sample image files, and the sample invite requests. Found by the
    demo data's own addresses (@example.test), so nothing else can match. Refuses to
    run when APP_ENV=production."""
    from sqlalchemy import delete

    from app.db.models import InviteCode, InviteRequest
    from app.ops import etsy_data

    etsy_data.refuse_in_production()
    async with sm() as session:
        accounts = (await session.execute(select(Tenant).where(Tenant.email.like(DEMO_ACCOUNTS)))).scalars().all()
        emails = [t.email for t in accounts]
        ids = [t.id for t in accounts]
        for tenant in accounts:
            await session.delete(tenant)  # everything of the account goes with it
        requests = await session.execute(delete(InviteRequest).where(InviteRequest.email.like(DEMO_REQUESTS)))
        codes = await session.execute(delete(InviteCode).where(InviteCode.bound_email.like(DEMO_REQUESTS)))
        await session.commit()
    if storage is not None and accounts:
        storage.delete_prefix("demo-shots")  # the sample images the seed drew
        for tenant_id in ids:  # what was uploaded to the demo accounts (batches, mockup templates)
            storage.delete_prefix(str(tenant_id))
    return (
        f"Removed {len(emails)} demo account(s) ({', '.join(emails) or 'none'}), "
        f"{requests.rowcount or 0} sample invite request(s) and {codes.rowcount or 0} invite code(s) made for them."
    )


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
    shop_check = sub.add_parser(
        "profile-shop-check",
        help="READ ONLY: an account's profiles against its shops (mapped ids or missing), token states, half-made drafts",
    )
    shop_check.add_argument("email")
    occluders = sub.add_parser(
        "mockup-occluders",
        help="template photos prepared before the in-front fix: analyse again what is in front (areas, confirmations, brushes kept); --apply",
    )
    occluders.add_argument("--apply", action="store_true", help="analyse them (default: only count)")
    occluders.add_argument("--email", default=None, help="only this account's photos")
    redetect = sub.add_parser(
        "mockup-redetect",
        help="template photos: suggest print areas again with the current geometry (confirmed areas kept); --apply",
    )
    redetect.add_argument("--apply", action="store_true", help="analyse them (default: only count)")
    redetect.add_argument("--email", default=None, help="only this account's photos")
    colours = sub.add_parser(
        "mockup-colours",
        help="template photos without a light/dark colour group (design variants): measure them; --apply",
    )
    colours.add_argument("--apply", action="store_true", help="measure them (default: only count)")
    colours.add_argument("--email", default=None, help="only this account's photos")
    settings_map = sub.add_parser(
        "resolve-shop-settings",
        help="profile links filled from the shop setting map; what is still missing resolved again; --apply",
    )
    settings_map.add_argument("--apply", action="store_true", help="write and queue (default: only count)")
    settings_map.add_argument("--email", default=None, help="only this account")
    single = sub.add_parser(
        "mockup-single-area",
        help="waiting template photos with several suggested areas: keep the main garment's (confirmed untouched); --apply",
    )
    single.add_argument("--apply", action="store_true", help="change them (default: only count)")
    single.add_argument("--email", default=None, help="only this account's templates")
    layouts = sub.add_parser(
        "mockup-layouts",
        help="waiting template photos: set each photo's layout and refresh its suggestions (confirmed untouched); --apply",
    )
    layouts.add_argument("--apply", action="store_true", help="analyse them (default: only count)")
    layouts.add_argument("--email", default=None, help="only this account's photos")
    occl = sub.add_parser(
        "mockup-occlusion",
        help="template photos: re-check what is in front of the design with the occlusion guards; waiting photos "
        "change with --apply, confirmed ones are only listed",
    )
    occl.add_argument("--apply", action="store_true", help="update waiting photos (default: only report)")
    occl.add_argument("--email", default=None, help="only this account's photos")
    failures = sub.add_parser(
        "draft-failures",
        help="one account's failed and half-made drafts, one per design and shop, with a summary per action: shop, "
        "listing id and where it was found, step, Etsy's answer, what was sent (names and kinds only), which draft "
        "replaced it; read only",
    )
    failures.add_argument("email")
    failures.add_argument("--since", default=None, help="only drafts from this day on (YYYY-MM-DD)")
    failures.add_argument("--check-etsy", action="store_true",
                          help="also READ each listing on Etsy (draft, active or gone) and the shop's drafts near each "
                          "failure without a listing id; rate limited, counted, never writes (run on the server)")
    report = sub.add_parser("sales-report", help="where an account's sales data stands (Analytics diagnosis)")
    report.add_argument("email")
    sub.add_parser("oauth-check", help="what the app sends Etsy to connect a shop, and what is wrong with it")
    stats_check = sub.add_parser(
        "listing-stats-check",
        help="ONE read-only Etsy request: are views and num_favorers in a shop's listing data (counts only)",
    )
    stats_check.add_argument("shop", help="the shop's id in the app, its Etsy shop id, or the account's e-mail")
    originals_cmd = sub.add_parser("storage-originals", help="delete the original uploads stored before only processed copies were kept (dry run unless --apply)")
    originals_cmd.add_argument("--apply", action="store_true", help="delete them (default: only count)")
    sub.add_parser("storage-report", help="stored image files per account and kind, their ages and whether they are still needed")
    sub.add_parser("draft-cost", help="Etsy requests per draft: measured average, the estimate used, the worst case")
    llm_cmd = sub.add_parser(
        "llm-check",
        help="one real vision and content call per model on a made-up design: does the provider accept what the app sends (costs a few cents)",
    )
    llm_cmd.add_argument("models", nargs="*", help="model ids (default: the configured VISION_MODEL and CONTENT_MODEL)")
    llm_cmd.add_argument("--supported", action="store_true", help="check every supported model")
    purge_cmd = sub.add_parser(
        "purge-etsy-data",
        help="delete ALL stored Etsy data for every account (the 'Etsy access ended' runbook); a dry run unless --apply",
    )
    purge_cmd.add_argument("--apply", action="store_true", help="delete (default: only count what would go)")
    scrub_cmd = sub.add_parser(
        "scrub-etsy-data",
        help="NON-PRODUCTION only: wipe Etsy-derived data and make shop names anonymous; a dry run unless --apply",
    )
    scrub_cmd.add_argument("--apply", action="store_true", help="wipe (default: only count what would go)")
    demo = sub.add_parser("seed-demo", help="NON-PRODUCTION only: add one synthetic demo account with sample data")
    demo.add_argument("--admin", action="store_true", help="make it an admin and add sample invite requests (for the layout test)")
    demo.add_argument("--remove", action="store_true", help="delete the demo accounts, their sample files and the sample invite requests")
    demo.add_argument("--shops", type=int, default=None, help="how many shops (default 9)")
    demo.add_argument("--groups", type=int, default=None, help="how many shop groups (default 2; none with one shop)")
    args = parser.parse_args(argv)
    if args.command == "oauth-check":
        print(oauth_check())
        return 0

    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    if args.command == "llm-check":
        from app.pipeline.llm import SUPPORTED_MODELS

        report_text, fine = asyncio.run(llm_check(list(SUPPORTED_MODELS) if args.supported else args.models or None))
        print(report_text)
        return 0 if fine else 1
    if args.command == "create-admin":
        print(asyncio.run(create_admin(sm, args.email, ask_password=prompt_password,
                                       set_password=args.set_password)))
    elif args.command == "demote-admin":
        print(asyncio.run(demote_admin(sm, args.email)))
    elif args.command == "sales-report":
        print(asyncio.run(sales_report(sm, args.email)))
    elif args.command == "listing-stats-check":
        print(asyncio.run(listing_stats_check(sm, args.shop)))
    elif args.command == "storage-originals":
        print(asyncio.run(storage_originals(sm, apply=args.apply)))
    elif args.command == "storage-report":
        print(asyncio.run(storage_report(sm)))
    elif args.command == "draft-cost":
        print(asyncio.run(draft_cost(sm)))
    elif args.command == "mockup-occluders":
        from app.api.deps import get_storage
        from app.ops.mockup_occluders import run as occluders_run

        print(asyncio.run(occluders_run(sm, get_storage(), apply=args.apply, email=args.email)))
    elif args.command == "mockup-redetect":
        from app.api.deps import get_storage
        from app.ops.mockup_redetect import run as redetect_run

        print(asyncio.run(redetect_run(sm, get_storage(), apply=args.apply, email=args.email)))
    elif args.command == "mockup-colours":
        from app.api.deps import get_storage
        from app.ops.mockup_colours import run as colours_run

        print(asyncio.run(colours_run(sm, get_storage(), apply=args.apply, email=args.email)))
    elif args.command == "resolve-shop-settings":
        from app.ops.shop_settings import run as settings_run

        print(asyncio.run(settings_run(sm, apply=args.apply, email=args.email)))
    elif args.command == "mockup-single-area":
        from app.ops.mockup_single_area import run as single_run

        print(asyncio.run(single_run(sm, apply=args.apply, email=args.email)))
    elif args.command == "mockup-layouts":
        from app.api.deps import get_storage
        from app.ops.mockup_layouts import run as layouts_run

        print(asyncio.run(layouts_run(sm, get_storage(), apply=args.apply, email=args.email)))
    elif args.command == "mockup-occlusion":
        from app.api.deps import get_storage
        from app.ops.mockup_occlusion import run as occlusion_run

        print(asyncio.run(occlusion_run(sm, get_storage(), apply=args.apply, email=args.email)))
    elif args.command == "draft-failures":
        from datetime import date as _date

        from app.ops.draft_failures import report as draft_failures_report

        since = _date.fromisoformat(args.since) if args.since else None
        print(asyncio.run(draft_failures_report(sm, args.email, since, check_etsy=args.check_etsy)))
    elif args.command == "profile-shop-check":
        from app.ops.profile_shop_check import report as profile_shop_report

        print(asyncio.run(profile_shop_report(sm, args.email)))
    elif args.command == "limits-report":
        print(asyncio.run(limits_report(sm, args.email)))
    elif args.command == "upload-retention":
        print(asyncio.run(upload_retention_report(sm, apply=args.apply)))
    elif args.command == "rebuild-publications":
        print(asyncio.run(rebuild_publications(sm, apply=args.apply)))
    elif args.command in ("purge-etsy-data", "scrub-etsy-data", "seed-demo"):
        from app.ops.etsy_data import Refused

        try:
            if args.command == "purge-etsy-data":
                print(asyncio.run(purge_etsy_data(sm, apply=args.apply)))
            elif args.command == "scrub-etsy-data":
                print(asyncio.run(scrub_etsy_data(sm, apply=args.apply)))
            elif args.remove:
                from app.api.deps import get_storage

                print(asyncio.run(remove_demo(sm, get_storage())))
            else:
                print(asyncio.run(seed_demo(sm, admin=args.admin, shops=args.shops, groups=args.groups)))
        except Refused as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        admins = asyncio.run(list_admins(sm))
        print("\n".join(admins) if admins else "no admins yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
