"""Operator screens: users, invites, usage (admin panel).

Every endpoint here depends on :func:`require_admin`, which checks the role on
the server for every request — the UI hiding a link is a courtesy, not a guard.
Anyone who is not a signed-in, active admin gets 404, including anonymous
callers, so the surface does not advertise itself.

What an admin can see is account *metadata*: email, shop name, dates, counts,
quota. Never another tenant's designs, batches or generated text — none of
these endpoints select from those tables beyond aggregate counts, and the
tenant-scoped endpoints elsewhere stay tenant-scoped for admins too.

Every change is written to the audit log in the same transaction.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.accounts import EMAIL_RE
from app.api.deps import get_quota, get_session, get_session_store
from app.core import audit
from app.core.config import get_settings
from app.core.invites import InviteState, hash_code, invite_state
from app.core.passwords import generate_temp_password, hash_password
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import (
    ApiUsage,
    InviteCode,
    ListingPublication,
    Job,
    JobStatus,
    Tenant,
    TenantStatus,
)
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops, app_shop_count, tenant_shop_limit
from app.workers.gate import SUSPENDED_MESSAGE

router = APIRouter(prefix="/api/admin", tags=["admin"])

HISTORY_DAYS = 7
_NOT_FOUND = HTTPException(status_code=404, detail="not found")


# --- Guard --------------------------------------------------------------------
async def require_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> Tenant:
    """The signed-in admin, or 404 for absolutely everyone else.

    Deliberately not built on current_tenant, which answers 401 to anonymous
    callers and so would confirm these endpoints exist. A temporary password
    does not unlock them either: that account must choose its own first.
    """
    record = await sessions.read(request.cookies.get(SESSION_COOKIE, ""))
    if record is None:
        raise _NOT_FOUND
    tenant = await session.get(Tenant, record.tenant_id)
    if (
        tenant is None
        or not tenant.is_admin
        or tenant.status is not TenantStatus.active
        or tenant.must_change_password
    ):
        raise _NOT_FOUND
    return tenant


async def _target(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="no such account")
    return tenant


def _not_self(admin: Tenant, target: Tenant, message: str) -> None:
    if admin.id == target.id:
        raise HTTPException(status_code=409, detail=message)


# --- Users ----------------------------------------------------------------------
class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool
    status: str  # active | suspended
    must_change_password: bool
    created_at: datetime
    shop_name: str | None  # the shops' names, comma-separated
    shop_connected: bool
    shops: list[str]  # names only: an admin never sees a shop's listings or profiles
    shops_used: int
    shops_limit: int
    shops_limit_custom: bool  # an admin override of MAX_SHOPS_PER_TENANT
    listings_published: int
    quota_used_today: int
    daily_quota: int


class ShopLimitUpdate(BaseModel):
    # None = back to the default (MAX_SHOPS_PER_TENANT).
    max_shops: int | None = Field(default=None, ge=1)


class QuotaUpdate(BaseModel):
    daily_quota: int = Field(ge=0)


class TempPasswordIssued(BaseModel):
    """The only time the temporary password is readable."""

    id: uuid.UUID
    email: str
    temporary_password: str


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> list[AdminUserOut]:
    tenants = (await session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    out = [await _user_out(session, quota, t) for t in tenants]
    return out


@router.post("/users/{tenant_id}/suspend", response_model=AdminUserOut)
async def suspend_user(
    tenant_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    target = await _target(session, tenant_id)
    _not_self(admin, target, "you cannot suspend your own account")
    if target.status is not TenantStatus.suspended:
        target.status = TenantStatus.suspended
        # Queued work is cancelled, not left to run. A job already running
        # finishes its current step; anything still in the queue is also
        # refused by the worker, which re-checks the account before every job.
        cancelled = await session.execute(
            update(Job)
            .where(Job.tenant_id == target.id, Job.status == JobStatus.queued)
            .values(
                status=JobStatus.cancelled,
                last_error=SUSPENDED_MESSAGE,
                paused_reason=None,
                finished_at=datetime.now(timezone.utc),
            )
        )
        audit.record(
            session,
            "user.suspended",
            actor=admin,
            target_tenant_id=target.id,
            cancelled_jobs=cancelled.rowcount or 0,
        )
        await session.commit()
    # Immediately, not at the next request: every open session ends now.
    await sessions.destroy_all(target.id)
    return await _one(session, quota, target.id)


@router.post("/users/{tenant_id}/reactivate", response_model=AdminUserOut)
async def reactivate_user(
    tenant_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    target = await _target(session, tenant_id)
    if target.status is not TenantStatus.active:
        target.status = TenantStatus.active
        audit.record(session, "user.reactivated", actor=admin, target_tenant_id=target.id)
        await session.commit()
    return await _one(session, quota, target.id)


@router.post("/users/{tenant_id}/temporary-password", response_model=TempPasswordIssued)
async def issue_temporary_password(
    tenant_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> TempPasswordIssued:
    """Replace the password with a one-off; they must choose their own at next login."""
    target = await _target(session, tenant_id)
    _not_self(admin, target, "use Change password for your own account")
    temp = generate_temp_password()
    target.password_hash = hash_password(temp)
    target.must_change_password = True
    audit.record(session, "user.temporary_password", actor=admin, target_tenant_id=target.id)
    await session.commit()
    await sessions.destroy_all(target.id)
    return TempPasswordIssued(id=target.id, email=target.email, temporary_password=temp)


@router.put("/users/{tenant_id}/quota", response_model=AdminUserOut)
async def set_user_quota(
    tenant_id: uuid.UUID,
    body: QuotaUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """Change one account's daily ceiling. It can never exceed the app-wide budget."""
    ceiling = get_settings().global_daily_limit
    if body.daily_quota > ceiling:
        raise HTTPException(
            status_code=422, detail=f"cannot exceed the app-wide limit of {ceiling:,} a day"
        )
    target = await _target(session, tenant_id)
    previous = target.daily_quota
    if previous != body.daily_quota:
        target.daily_quota = body.daily_quota
        audit.record(
            session,
            "user.quota_changed",
            actor=admin,
            target_tenant_id=target.id,
            previous=previous,
            new=body.daily_quota,
        )
        await session.commit()
    return await _one(session, quota, target.id)


async def _user_out(session: AsyncSession, quota: DailyQuota, t: Tenant) -> AdminUserOut:
    """Account metadata and counts only: an admin sees how much, never what."""
    from app.api.shops import shop_label

    shops = [shop_label(c) for c in await active_shops(session, t.id)]
    published = await session.scalar(
        select(func.count())
        .select_from(ListingPublication)
        .where(ListingPublication.tenant_id == t.id, ListingPublication.state == "active")
    )
    used_today, _ = await quota.usage(t.id)
    return AdminUserOut(
        id=t.id,
        email=t.email,
        is_admin=t.is_admin,
        status=t.status.value,
        must_change_password=t.must_change_password,
        created_at=t.created_at,
        shop_name=", ".join(shops) or None,
        shop_connected=bool(shops),
        shops=shops,
        shops_used=len(shops),
        shops_limit=tenant_shop_limit(t),
        shops_limit_custom=t.max_shops is not None,
        listings_published=int(published or 0),
        quota_used_today=used_today,
        daily_quota=t.daily_quota,
    )


async def _one(session: AsyncSession, quota: DailyQuota, tenant_id: uuid.UUID) -> AdminUserOut:
    t = await session.get(Tenant, tenant_id)
    await session.refresh(t)
    return await _user_out(session, quota, t)


@router.put("/users/{tenant_id}/shops", response_model=AdminUserOut)
async def set_user_shop_limit(
    tenant_id: uuid.UUID,
    body: ShopLimitUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """How many shops this account may connect (v5 §E). Never above the app-wide ceiling.

    Lowering it below the shops already connected disconnects nothing; it only
    stops new ones.
    """
    app_limit = get_settings().max_shops_app_wide
    if body.max_shops is not None and body.max_shops > app_limit:
        raise HTTPException(
            status_code=422, detail=f"cannot exceed the app-wide limit of {app_limit} shops"
        )
    target = await _target(session, tenant_id)
    previous = target.max_shops
    if previous != body.max_shops:
        target.max_shops = body.max_shops
        audit.record(
            session,
            "user.shop_limit_changed",
            actor=admin,
            target_tenant_id=target.id,
            previous=previous,
            new=body.max_shops,
        )
        await session.commit()
    return await _one(session, quota, target.id)


# --- Invites --------------------------------------------------------------------
class InviteOut(BaseModel):
    id: uuid.UUID
    note: str | None
    bound_email: str | None
    state: InviteState
    created_at: datetime
    expires_at: datetime | None
    used_at: datetime | None
    used_by_email: str | None
    created_by_email: str | None


class InviteCreate(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    note: str | None = Field(default=None, max_length=200)
    # None = never expires.
    expires_in_days: int | None = Field(default=30, ge=1, le=365)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        if not EMAIL_RE.match(cleaned):
            raise ValueError("not a valid email address")
        return cleaned.casefold()


class InviteIssued(BaseModel):
    """The only time the code is readable: only its hash is stored."""

    code: str
    invite: InviteOut


async def _invite_out(session: AsyncSession, invite: InviteCode) -> InviteOut:
    async def email_of(tenant_id: uuid.UUID | None) -> str | None:
        if tenant_id is None:
            return None
        t = await session.get(Tenant, tenant_id)
        return t.email if t else None

    return InviteOut(
        id=invite.id,
        note=invite.note,
        bound_email=invite.bound_email,
        state=invite_state(invite),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        used_at=invite.used_at,
        used_by_email=await email_of(invite.used_by_tenant_id),
        created_by_email=await email_of(invite.created_by_tenant_id),
    )


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[InviteOut]:
    rows = (
        await session.execute(select(InviteCode).order_by(InviteCode.created_at.desc()))
    ).scalars().all()
    return [await _invite_out(session, i) for i in rows]


@router.post("/invites", response_model=InviteIssued, status_code=201)
async def create_invite(
    body: InviteCreate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteIssued:
    if body.email is not None:
        taken = await session.scalar(
            select(func.count()).select_from(Tenant).where(func.lower(Tenant.email) == body.email)
        )
        if taken:
            raise HTTPException(status_code=409, detail="that address already has an account")

    code = secrets.token_urlsafe(18)
    invite = InviteCode(
        code_hash=hash_code(code),
        note=body.note,
        bound_email=body.email,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
            if body.expires_in_days is not None
            else None
        ),
        created_by_tenant_id=admin.id,
    )
    session.add(invite)
    await session.flush()
    audit.record(
        session,
        "invite.created",
        actor=admin,
        target_invite_id=invite.id,
        bound=body.email is not None,  # whether, never to whom
        expires_in_days=body.expires_in_days,
    )
    await session.commit()
    await session.refresh(invite)
    return InviteIssued(code=code, invite=await _invite_out(session, invite))


@router.post("/invites/{invite_id}/revoke", response_model=InviteOut)
async def revoke_invite(
    invite_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteOut:
    invite = await session.get(InviteCode, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="no such invite")
    state = invite_state(invite)
    if state is not InviteState.unused:
        raise HTTPException(status_code=409, detail=f"only an unused code can be revoked; this one is {state.value}")
    invite.revoked_at = datetime.now(timezone.utc)
    audit.record(session, "invite.revoked", actor=admin, target_invite_id=invite.id)
    await session.commit()
    await session.refresh(invite)
    return await _invite_out(session, invite)


# --- Usage ----------------------------------------------------------------------
class DayCount(BaseModel):
    date: str
    count: int


class TenantUsage(BaseModel):
    id: uuid.UUID
    email: str
    used_today: int
    daily_quota: int
    # Set when this tenant has work waiting for the reset today (global_quota /
    # tenant_quota), whichever stopped it.
    paused_reason: str | None = None
    history: list[DayCount]


class UsageOut(BaseModel):
    usage_date: str
    global_used: int
    global_limit: int
    global_remaining: int
    # New jobs stop being started at this app-wide count (production-spec C).
    pause_at: int
    # Connected shops across all accounts, against MAX_SHOPS_APP_WIDE (v5 §E).
    shops_used: int
    shops_limit: int
    history: list[DayCount]  # app-wide, oldest first; today from the live counter
    tenants: list[TenantUsage]  # busiest first


@router.get("/usage", response_model=UsageOut)
async def usage(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> UsageOut:
    """The scarce resource: today's app-wide Etsy budget, per tenant, and 7 days of it."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=HISTORY_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(HISTORY_DAYS)]

    # api_usage is written in batches and lags; completed days come from it,
    # today from the live counters the quota itself enforces against.
    past: dict[tuple[uuid.UUID, date], int] = {
        (row[0], row[1]): int(row[2])
        for row in (
            await session.execute(
                select(ApiUsage.tenant_id, ApiUsage.usage_date, ApiUsage.request_count).where(
                    ApiUsage.usage_date >= start, ApiUsage.usage_date < today
                )
            )
        ).all()
    }

    tenants = (await session.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    global_used = 0
    rows: list[TenantUsage] = []
    for t in tenants:
        used_today, global_used = await quota.usage(t.id)
        history = [
            DayCount(
                date=d.isoformat(),
                count=used_today if d == today else past.get((t.id, d), 0),
            )
            for d in days
        ]
        rows.append(
            TenantUsage(
                id=t.id,
                email=t.email,
                used_today=used_today,
                daily_quota=t.daily_quota,
                paused_reason=await quota.paused_reason(t.id),
                history=history,
            )
        )
    if not tenants:
        _, global_used = await quota.usage(uuid.uuid4())

    history = [
        DayCount(
            date=d.isoformat(),
            count=global_used if d == today else sum(past.get((t.id, d), 0) for t in tenants),
        )
        for d in days
    ]
    limit = get_settings().global_daily_limit
    rows.sort(key=lambda r: (-r.used_today, r.email))
    return UsageOut(
        usage_date=today.isoformat(),
        global_used=global_used,
        global_limit=limit,
        global_remaining=max(0, limit - global_used),
        pause_at=quota.pause_at,
        shops_used=await app_shop_count(session),
        shops_limit=get_settings().max_shops_app_wide,
        history=history,
        tenants=rows,
    )
