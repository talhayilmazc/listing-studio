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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.accounts import EMAIL_RE
from app.api.deps import get_quota, get_redis, get_session, get_session_store, get_storage
from app.compliance.trademarks import filter_on
from app.core import allowance, audit
from app.core.config import get_settings
from app.core.invites import InviteState, hash_code, invite_state
from app.core.passwords import generate_temp_password, hash_password
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import (
    AppSetting,
    ApiUsage,
    AiCall,
    InviteCode,
    InviteRequest,
    ListingPublication,
    Job,
    JobStatus,
    Tenant,
    TenantStatus,
)
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops, app_shop_count, tenant_shop_limit
from app.workers.gate import SUSPENDED_MESSAGE

from app.etsy.categories import LABELS

from decimal import Decimal
from redis.asyncio import Redis

from app.core import ai_meter, ai_prices, disk, limits, storage_cap
from app.pipeline import upload_retention
from app.pipeline.storage import Storage
from app.api import ai_series, sales_reread

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
class SpendOut(BaseModel):
    category: str
    label: str
    counted: int = 0
    upkeep: int = 0


def _spend(raw: dict[str, dict[str, int]]) -> list[SpendOut]:
    rows = [
        SpendOut(category=c, label=LABELS.get(c, c), counted=v.get("own", 0), upkeep=v.get("upkeep", 0))
        for c, v in raw.items()
    ]
    return sorted(rows, key=lambda r: r.counted + r.upkeep, reverse=True)


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
    # Stored image files allowed (core/storage_cap.py); custom: an admin's own number.
    storage_cap_bytes: int = 0
    storage_cap_custom: bool = False
    listings_published: int
    # "Etsy requests today": the account's ceiling, as the seller's own screens
    # show it (core/limits.py). ``follows_default`` false: an admin set its number.
    etsy: schemas.EtsyCeilingOut
    # What today's and yesterday's Etsy requests were spent on, largest first.
    # ``counted`` requests go toward the account's ceiling; ``upkeep`` ones (the
    # app keeping the shop's data current) do not.
    spent_today: list[SpendOut] = []
    spent_yesterday: list[SpendOut] = []
    # The trademark filter (v7 §A4): the admin override (None = the seller's own
    # choice), the seller's own choice and when they last changed it, and what's in force.
    trademark_filter: bool | None = None
    trademark_filter_seller: bool = True
    trademark_filter_changed_at: datetime | None = None
    trademark_filter_effective: bool = True
    # The product allowance in force, its usage this period and when it resets.
    allowance: schemas.AllowanceOut | None = None
    # Features an admin turned on for this account (v7 §B).
    features: dict[str, bool] = {}


class FeaturesUpdate(BaseModel):
    # Only the named features change; the rest stay as they are.
    features: dict[str, bool]


#: Features an admin can turn on per account.
KNOWN_FEATURES = frozenset({"own_patterns"})


class TrademarkFilterUpdate(BaseModel):
    # None = back to the seller's own choice.
    enabled: bool | None = None


class ShopLimitUpdate(BaseModel):
    # None = back to the default (MAX_SHOPS_PER_TENANT).
    max_shops: int | None = Field(default=None, ge=1)


class AllowanceUpdate(BaseModel):
    # Both set: the account's own allowance. Both None: it follows the system default.
    amount: int | None = Field(default=None, ge=0, le=1_000_000)
    period: Literal["daily", "weekly", "monthly"] | None = None


class AllowanceDefault(BaseModel):
    amount: int = Field(ge=0, le=1_000_000)
    period: Literal["daily", "weekly", "monthly"]


class QuotaUpdate(BaseModel):
    # The account's own Etsy requests per day. None: follow the default.
    daily_quota: int | None = Field(default=None, ge=0)


class StorageCapUpdate(BaseModel):
    # The account's stored-image cap in GB. None: follow the default (STORAGE_CAP_GB).
    gb: float | None = Field(default=None, gt=0, le=1000)


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
    """Give one account its own Etsy requests per day, or (None) put it back on
    the default. It can never exceed the app-wide budget."""
    ceiling = get_settings().global_daily_limit
    if body.daily_quota is not None and body.daily_quota > ceiling:
        raise HTTPException(
            status_code=422, detail=f"cannot exceed the app-wide limit of {ceiling:,} a day"
        )
    target = await _target(session, tenant_id)
    previous = target.etsy_ceiling_override
    if previous != body.daily_quota:
        target.etsy_ceiling_override = body.daily_quota
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


@router.put("/users/{tenant_id}/storage-cap", response_model=AdminUserOut)
async def set_storage_cap(
    tenant_id: uuid.UUID,
    body: StorageCapUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """Give one account its own cap on stored image files, or (None) put it back on
    the default. Lowering it refuses new uploads only; nothing stored is deleted."""
    target = await _target(session, tenant_id)
    new = int(body.gb * storage_cap.GB) if body.gb is not None else None
    previous = target.storage_cap_bytes
    if previous != new:
        target.storage_cap_bytes = new
        audit.record(session, "user.storage_cap_changed", actor=admin, target_tenant_id=target.id,
                     previous=previous, new=new)
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
        storage_cap_bytes=storage_cap.limit(t),
        storage_cap_custom=t.storage_cap_bytes is not None,
        listings_published=int(published or 0),
        spent_today=_spend(await quota.spending(t.id)),
        spent_yesterday=_spend(await quota.spending(t.id, days_ago=1)),
        etsy=schemas.EtsyCeilingOut(**(await limits.etsy_ceiling(quota, t)).out()),
        trademark_filter=t.trademark_filter,
        trademark_filter_seller=t.trademark_filter_seller,
        trademark_filter_changed_at=t.trademark_filter_changed_at,
        features={k: bool(v) for k, v in (t.features or {}).items()},
        trademark_filter_effective=filter_on(t),
        allowance=schemas.AllowanceOut(**allowance.status_out(await allowance.status(session, t))),
    )


async def _one(session: AsyncSession, quota: DailyQuota, tenant_id: uuid.UUID) -> AdminUserOut:
    t = await session.get(Tenant, tenant_id)
    await session.refresh(t)
    return await _user_out(session, quota, t)


@router.put("/users/{tenant_id}/allowance", response_model=AdminUserOut)
async def set_user_allowance(
    tenant_id: uuid.UUID,
    body: AllowanceUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """One seller's product allowance: an amount and a period (None = the default).

    Applies at once: usage is counted over the new period from what is already
    recorded, so nothing used so far is lost or forgiven.
    """
    if (body.amount is None) != (body.period is None):
        # An allowance is an amount and a period together: half of one would mix
        # the account's amount with the default's period (or the other way round).
        raise HTTPException(status_code=422, detail="give both an amount and a period, or neither to follow the default")
    target = await _target(session, tenant_id)
    previous = {"amount": target.allowance_amount, "period": target.allowance_period}
    new = {"amount": body.amount, "period": body.period}
    if previous != new:
        target.allowance_amount, target.allowance_period = body.amount, body.period
        audit.record(
            session, "user.allowance_changed", actor=admin, target_tenant_id=target.id,
            previous=previous, new=new,
        )
        await session.commit()
    return await _one(session, quota, target.id)


@router.get("/allowance-default", response_model=AllowanceDefault)
async def get_allowance_default(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AllowanceDefault:
    amount, period = await allowance.system_default(session)
    return AllowanceDefault(amount=amount, period=period)


@router.put("/allowance-default", response_model=AllowanceDefault)
async def set_allowance_default(
    body: AllowanceDefault,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AllowanceDefault:
    """The allowance of every seller without their own. Applies at once, audited."""
    amount, period = await allowance.system_default(session)
    previous = {"amount": amount, "period": period}
    new = {"amount": body.amount, "period": body.period}
    if previous != new:
        row = await session.get(AppSetting, allowance.DEFAULT_KEY)
        if row is None:
            session.add(AppSetting(key=allowance.DEFAULT_KEY, value=new))
        else:
            row.value = new
        audit.record(session, "app.allowance_default_changed", actor=admin, previous=previous, new=new)
        await session.commit()
    return body


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


@router.put("/users/{tenant_id}/features", response_model=AdminUserOut)
async def set_user_features(
    tenant_id: uuid.UUID,
    body: FeaturesUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """Turn features on or off for one account (v7 §B), audited."""
    unknown = set(body.features) - KNOWN_FEATURES
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown feature: {', '.join(sorted(unknown))}")
    target = await _target(session, tenant_id)
    previous = dict(target.features or {})
    merged = {**previous, **body.features}
    if merged != previous:
        target.features = merged
        audit.record(
            session,
            "user.features_changed",
            actor=admin,
            target_tenant_id=target.id,
            previous=previous,
            new=merged,
        )
        await session.commit()
    return await _one(session, quota, target.id)


@router.put("/users/{tenant_id}/trademark-filter", response_model=AdminUserOut)
async def set_user_trademark_filter(
    tenant_id: uuid.UUID,
    body: TrademarkFilterUpdate,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    quota: DailyQuota = Depends(get_quota),
) -> AdminUserOut:
    """Override the trademark filter for one account (v7 §A4).

    It wins over the seller's own choice in Settings. Off, brand and character
    names may appear in that seller's listings; the risk under Etsy's
    intellectual property policy is theirs. None returns the account to the
    seller's own choice.
    """
    target = await _target(session, tenant_id)
    previous = target.trademark_filter
    if previous != body.enabled:
        target.trademark_filter = body.enabled
        audit.record(
            session,
            "user.trademark_filter_changed",
            actor=admin,
            target_tenant_id=target.id,
            previous=previous,
            new=body.enabled,
            by="admin",
        )
        await session.flush()
        # Their listings' findings follow the new setting at once. Nothing is
        # read or shown to the admin: the scan runs on the seller's own rows.
        from app.compliance.scanner import rescan_account

        await rescan_account(session, target.id)
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


# --- Invite requests (the public form) ---------------------------------------------
class InviteRequestOut(BaseModel):
    id: uuid.UUID
    email: str
    shop: str | None
    note: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None
    #: That address already has an account, so there is nothing to approve.
    has_account: bool = False


class InviteRequestApproved(BaseModel):
    """The code is readable here and only here, as when an invite is created."""

    code: str
    invite: InviteOut
    request: InviteRequestOut


async def _request_out(session: AsyncSession, row: InviteRequest) -> InviteRequestOut:
    taken = await session.scalar(
        select(func.count()).select_from(Tenant).where(func.lower(Tenant.email) == row.email)
    )
    return InviteRequestOut(
        id=row.id, email=row.email, shop=row.shop, note=row.note, status=row.status,
        created_at=row.created_at, decided_at=row.decided_at, has_account=bool(taken),
    )


async def _pending(session: AsyncSession, request_id: uuid.UUID) -> InviteRequest:
    row = await session.get(InviteRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such request")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"this request was already {row.status}")
    return row


@router.get("/invite-requests", response_model=list[InviteRequestOut])
async def list_invite_requests(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[InviteRequestOut]:
    """Pending first (oldest waiting longest), then the recently decided."""
    rows = (
        await session.execute(
            select(InviteRequest).order_by(
                (InviteRequest.status != "pending"), InviteRequest.created_at
            ).limit(200)
        )
    ).scalars().all()
    return [await _request_out(session, r) for r in rows]


@router.post("/invite-requests/{request_id}/approve", response_model=InviteRequestApproved)
async def approve_invite_request(
    request_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteRequestApproved:
    """Make an invite code that only this address can redeem (30 days)."""
    row = await _pending(session, request_id)
    taken = await session.scalar(
        select(func.count()).select_from(Tenant).where(func.lower(Tenant.email) == row.email)
    )
    if taken:
        raise HTTPException(status_code=409, detail="that address already has an account")
    code = secrets.token_urlsafe(18)
    invite = InviteCode(
        code_hash=hash_code(code),
        note=("Requested: " + row.shop)[:200] if row.shop else "Requested on the site",
        bound_email=row.email,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        created_by_tenant_id=admin.id,
    )
    session.add(invite)
    await session.flush()
    row.status, row.decided_at, row.decided_by_tenant_id, row.invite_id = (
        "approved", datetime.now(timezone.utc), admin.id, invite.id,
    )
    audit.record(session, "invite_request.approved", actor=admin, target_invite_id=invite.id)
    await session.commit()
    await session.refresh(invite)
    await session.refresh(row)
    return InviteRequestApproved(
        code=code, invite=await _invite_out(session, invite), request=await _request_out(session, row)
    )


@router.post("/invite-requests/{request_id}/decline", response_model=InviteRequestOut)
async def decline_invite_request(
    request_id: uuid.UUID,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteRequestOut:
    row = await _pending(session, request_id)
    row.status, row.decided_at, row.decided_by_tenant_id = "declined", datetime.now(timezone.utc), admin.id
    audit.record(session, "invite_request.declined", actor=admin)
    await session.commit()
    await session.refresh(row)
    return await _request_out(session, row)


# --- AI cost (ours; never shown to sellers) -----------------------------------------
class AiTotals(BaseModel):
    """Calls, tokens and cost over some set of calls."""

    calls: int = 0
    failed: int = 0
    listings: int = 0
    input_tokens: int = 0  # prompt tokens that were not cached
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: str = "0.000000"
    #: Some of these calls used a model with no price: the cost is a floor.
    unpriced: bool = False
    #: Cost divided by listings written, when any were.
    cost_per_listing_usd: str | None = None


class AiSeller(BaseModel):
    id: uuid.UUID | None  # None: our own calls (evaluations) and deleted accounts
    email: str | None
    today: AiTotals
    this_month: AiTotals


class AiPurpose(BaseModel):
    purpose: str
    label: str
    today: AiTotals
    this_month: AiTotals


class AiModelDay(AiTotals):
    model: str


class AiDay(AiTotals):
    day: str
    #: Per model, as the provider's console lists a day: for reconciling.
    models: list[AiModelDay] = []


class AiCallOut(BaseModel):
    at: datetime
    email: str | None
    purpose: str
    model: str
    ok: bool
    error: str | None
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: str | None
    duration_ms: int | None


class AiPriceOut(BaseModel):
    model: str
    input: str
    output: str
    cache_write: str
    cache_write_1h: str
    cache_read: str
    custom: bool  # set in the admin panel, not the built-in default


class AiCostOut(BaseModel):
    as_of: datetime
    today: AiTotals  # the UTC day, as the provider's console counts
    this_month: AiTotals
    sellers: list[AiSeller]
    purposes: list[AiPurpose]
    days: list[AiDay]  # the last 31 UTC days, newest first
    recent: list[AiCallOut]  # the last 50 calls
    prices: list[AiPriceOut]
    unpriced_models: list[str]


class AiPriceIn(BaseModel):
    model: str = Field(min_length=1, max_length=80)
    input: str
    output: str
    cache_write: str
    cache_write_1h: str
    cache_read: str


_SUMS = ("calls", "failed", "listings", "input_tokens", "output_tokens", "cache_write_tokens",
         "cache_write_1h_tokens", "cache_read_tokens")


def _totals(groups: list[dict[str, Any]], cls: type[AiTotals] = AiTotals, **extra: Any) -> Any:
    out = cls(**extra)
    cost = Decimal("0")
    for g in groups:
        for name in _SUMS:
            setattr(out, name, getattr(out, name) + int(g[name] or 0))
        cost += Decimal(str(g["cost"] or 0))
        if g["unpriced"]:
            out.unpriced = True
    out.cost_usd = f"{cost:.6f}"
    out.cost_per_listing_usd = f"{cost / out.listings:.6f}" if out.listings else None
    return out


async def _prices_out(session: AsyncSession) -> list[AiPriceOut]:
    stored = await session.get(AppSetting, ai_prices.KEY)
    custom = set((stored.value if stored else {}) or {})
    table = await ai_prices.load(session)
    return [AiPriceOut(model=m, custom=m in custom, **p.as_strings()) for m, p in sorted(table.items())]


@router.get("/ai-cost", response_model=AiCostOut)
async def ai_cost(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AiCostOut:
    """What the AI provider's work is costing us, live: today and this month,
    per seller and per purpose, per listing, and day by day per model so the
    total can be checked against the provider's console.

    Counts only; no listing text and no batch. This is the one place these
    figures are served, and only to an admin.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    month = today.replace(day=1)
    since = min(month, today - timedelta(days=30))
    # One row per (day, seller, purpose, model): small, whatever the call count.
    grouped = (
        await session.execute(
            select(
                AiCall.day, AiCall.tenant_id, AiCall.purpose, AiCall.model,
                func.count().label("calls"),
                func.sum(case((AiCall.ok.is_(False), 1), else_=0)).label("failed"),
                func.sum(AiCall.listings).label("listings"),
                func.sum(AiCall.input_tokens).label("input_tokens"),
                func.sum(AiCall.output_tokens).label("output_tokens"),
                func.sum(AiCall.cache_write_tokens).label("cache_write_tokens"),
                func.sum(AiCall.cache_write_1h_tokens).label("cache_write_1h_tokens"),
                func.sum(AiCall.cache_read_tokens).label("cache_read_tokens"),
                func.sum(AiCall.cost_usd).label("cost"),
                # A call with tokens but no cost: its model had no price.
                func.sum(case((and_(AiCall.cost_usd.is_(None), AiCall.input_tokens + AiCall.output_tokens > 0), 1), else_=0)).label("unpriced"),
            )
            .where(AiCall.day >= since)
            .group_by(AiCall.day, AiCall.tenant_id, AiCall.purpose, AiCall.model)
        )
    ).mappings().all()
    rows = [dict(r) for r in grouped]
    emails = {t.id: t.email for t in (await session.execute(select(Tenant))).scalars()}
    for r in rows:
        if r["tenant_id"] not in emails:
            r["tenant_id"] = None  # a deleted account, or our own calls

    def of(pred: Any) -> list[dict[str, Any]]:
        return [r for r in rows if pred(r)]

    is_today = lambda r: r["day"] == today  # noqa: E731
    in_month = lambda r: r["day"] >= month  # noqa: E731

    sellers = [
        AiSeller(
            id=tid, email=emails.get(tid) if tid else None,
            today=_totals(of(lambda r, tid=tid: r["tenant_id"] == tid and is_today(r))),
            this_month=_totals(of(lambda r, tid=tid: r["tenant_id"] == tid and in_month(r))),
        )
        for tid in {r["tenant_id"] for r in rows if in_month(r)}
    ]
    sellers.sort(key=lambda s: Decimal(s.this_month.cost_usd), reverse=True)
    purposes = [
        AiPurpose(
            purpose=p, label=ai_meter.PURPOSES.get(p, p),
            today=_totals(of(lambda r, p=p: r["purpose"] == p and is_today(r))),
            this_month=_totals(of(lambda r, p=p: r["purpose"] == p and in_month(r))),
        )
        for p in {r["purpose"] for r in rows if in_month(r)}
    ]
    purposes.sort(key=lambda p: Decimal(p.this_month.cost_usd), reverse=True)
    days = []
    for day in sorted({r["day"] for r in rows if r["day"] > today - timedelta(days=31)}, reverse=True):
        of_day = of(lambda r, day=day: r["day"] == day)
        days.append(
            _totals(
                of_day, AiDay, day=day.isoformat(),
                models=[
                    _totals([r for r in of_day if r["model"] == m], AiModelDay, model=m)
                    for m in sorted({r["model"] for r in of_day})
                ],
            )
        )
    recent = (await session.execute(select(AiCall).order_by(AiCall.at.desc()).limit(50))).scalars().all()
    table = await ai_prices.load(session)
    unpriced = (
        await session.execute(
            select(AiCall.model).where(AiCall.cost_usd.is_(None), AiCall.input_tokens + AiCall.output_tokens > 0).distinct()
        )
    ).scalars().all()
    return AiCostOut(
        as_of=now,
        today=_totals(of(is_today)),
        this_month=_totals(of(in_month)),
        sellers=sellers,
        purposes=purposes,
        days=days,
        recent=[
            AiCallOut(
                at=c.at, email=emails.get(c.tenant_id) if c.tenant_id else None, purpose=c.purpose, model=c.model,
                ok=c.ok, error=c.error, input_tokens=c.input_tokens, output_tokens=c.output_tokens,
                cache_write_tokens=c.cache_write_tokens + c.cache_write_1h_tokens, cache_read_tokens=c.cache_read_tokens,
                cost_usd=None if c.cost_usd is None else f"{c.cost_usd:.6f}", duration_ms=c.duration_ms,
            )
            for c in recent
        ],
        prices=await _prices_out(session),
        unpriced_models=sorted(m for m in unpriced if ai_prices.price_for(m, table) is None),
    )


@router.get("/ai-cost/series", response_model=ai_series.AiSeriesOut)
async def ai_cost_series(
    period: ai_series.Period = "daily",
    seller: str = "all",
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ai_series.AiSeriesOut:
    """AI cost over time, per seller: hourly, daily, weekly or monthly buckets in
    Istanbul time, for every seller or one ("none": our own runs), with the
    previous equal period to compare against. Counts and cost only."""
    try:
        who = ai_series.parse_seller(seller)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="seller is \"all\", \"none\" or an account id") from exc
    return await ai_series.build(session, period, who)


@router.put("/ai-prices", response_model=list[AiPriceOut])
async def set_ai_price(
    body: AiPriceIn,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AiPriceOut]:
    """Set one model's rates (USD per million tokens). Calls already costed keep
    their cost; calls of that model that had no price are costed now."""
    try:
        rates = ai_prices.validate(body.model_dump(exclude={"model"}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    model = body.model.strip()
    row = await session.get(AppSetting, ai_prices.KEY)
    stored = dict((row.value if row else {}) or {})
    previous = stored.get(model)
    stored[model] = rates
    if row is None:
        session.add(AppSetting(key=ai_prices.KEY, value=stored))
    else:
        row.value = stored
    await session.flush()
    table = await ai_prices.load(session)
    waiting = (await session.execute(select(AiCall).where(AiCall.cost_usd.is_(None)))).scalars().all()
    costed = 0
    for call in waiting:
        call.cost_usd = ai_prices.cost_of(call, table)
        costed += call.cost_usd is not None
    audit.record(session, "app.ai_price_changed", actor=admin, model=model, previous=previous, new=rates, costed=costed)
    await session.commit()
    return await _prices_out(session)


@router.delete("/ai-prices/{model}", response_model=list[AiPriceOut])
async def reset_ai_price(
    model: str,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AiPriceOut]:
    """Back to the built-in rates for this model (or no price, if it has none)."""
    row = await session.get(AppSetting, ai_prices.KEY)
    stored = dict((row.value if row else {}) or {})
    if model in stored and row is not None:
        previous = stored.pop(model)
        row.value = stored
        audit.record(session, "app.ai_price_changed", actor=admin, model=model, previous=previous, new=None)
        await session.commit()
    return await _prices_out(session)


# --- Disk (the server's, by category) and upload retention ------------------------------
class DiskCategory(BaseModel):
    key: str
    label: str
    note: str
    #: None: not known (the host's hourly report has not arrived).
    bytes: int | None
    files: int | None = None


class UploadRetentionDays(BaseModel):
    #: Image files are deleted this many days after the group has drafts in every
    #: target shop and nothing is pending (schedule, distribution, replace).
    drafted_days: int = Field(ge=1, le=upload_retention.MAX_DAYS)
    #: Any other group: this many days after it was last worked on.
    unpublished_days: int = Field(ge=1, le=upload_retention.MAX_DAYS)


class UploadRetentionRun(BaseModel):
    at: datetime
    applied: bool  # False: a dry run, nothing was deleted
    drafted_groups: int
    unpublished_groups: int
    images: int
    files: int
    freed_bytes: int
    thumbnails: int
    thumbnail_bytes: int
    waiting: int


class DiskOut(BaseModel):
    as_of: datetime
    total_bytes: int | None
    free_bytes: int | None
    categories: list[DiskCategory]
    #: When deploy/disk-check.sh last reported backups and Docker; None: never.
    host_reported_at: datetime | None
    host_fresh: bool
    retention: UploadRetentionDays
    retention_defaults: UploadRetentionDays
    #: False: the daily job only counts (UPLOAD_RETENTION_APPLY=false).
    retention_applies: bool
    last_run: UploadRetentionRun | None


def _days(policy: upload_retention.Policy) -> UploadRetentionDays:
    return UploadRetentionDays(drafted_days=policy.drafted_days, unpublished_days=policy.unpublished_days)


@router.get("/sales-reread", response_model=sales_reread.RereadAdminReport)
async def sales_reread_progress(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> sales_reread.RereadAdminReport:
    """The one-time sales re-read across all accounts: tonight's share of the
    budget, and each shop's progress. Counts and dates only."""
    return await sales_reread.for_admin(session)


@router.get("/disk", response_model=DiskOut)
async def disk_usage(
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    redis: Redis = Depends(get_redis),
) -> DiskOut:
    """What is using the server's disk, by category, and what upload retention
    last freed. Sizes and counts only: no file, no name, no seller."""
    snapshot = await disk.snapshot(session, storage, redis)
    last = await upload_retention.last(session)
    run = None
    if last:
        try:
            run = UploadRetentionRun(**{k: last[k] for k in UploadRetentionRun.model_fields})
        except (KeyError, ValueError):
            run = None  # written by an older version: shown again after the next run
    return DiskOut(
        as_of=datetime.fromtimestamp(snapshot.at, timezone.utc),
        total_bytes=snapshot.total_bytes,
        free_bytes=snapshot.free_bytes,
        categories=[
            DiskCategory(key=c.key, label=disk.LABELS[c.key], note=disk.NOTES[c.key], bytes=c.bytes, files=c.files)
            for c in snapshot.categories
        ],
        host_reported_at=datetime.fromtimestamp(snapshot.host_at, timezone.utc) if snapshot.host_at else None,
        host_fresh=snapshot.host_fresh,
        retention=_days(await upload_retention.policy(session)),
        retention_defaults=_days(upload_retention.defaults()),
        retention_applies=get_settings().upload_retention_apply,
        last_run=run,
    )


@router.put("/upload-retention", response_model=UploadRetentionDays)
async def set_upload_retention(
    body: UploadRetentionDays,
    admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UploadRetentionDays:
    """How long image files are kept. Applies from the next daily run; audited.
    Shortening it deletes more at that run, and deleted files do not come back."""
    previous = _days(await upload_retention.policy(session)).model_dump()
    new = upload_retention.validate(body.model_dump())
    if previous != new:
        row = await session.get(AppSetting, upload_retention.KEY)
        if row is None:
            session.add(AppSetting(key=upload_retention.KEY, value=new))
        else:
            row.value = new
        audit.record(session, "app.upload_retention_changed", actor=admin, previous=previous, new=new)
        await session.commit()
    return UploadRetentionDays(**new)


# --- Usage ----------------------------------------------------------------------
class DayCount(BaseModel):
    date: str
    count: int


class TenantUsage(BaseModel):
    id: uuid.UUID
    email: str
    # The account's own requests today and its ceiling (core/limits.py).
    used_today: int
    limit: int
    follows_default: bool
    # Set when this tenant has work waiting for the reset today (global_quota /
    # tenant_quota), whichever stopped it.
    paused_reason: str | None = None
    history: list[DayCount]


class UsageOut(BaseModel):
    """The app's Etsy budget (admin only): everyone's requests, upkeep included."""

    usage_date: str
    global_used: int
    global_limit: int
    global_remaining: int
    # New jobs stop being started at this app-wide count (production-spec C).
    pause_at: int
    # What may still be spent before new work pauses, and when both counters reset.
    until_pause: int
    resets_at: datetime
    # What an account follows unless it has its own number.
    ceiling_default: int
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
        account = await limits.etsy_ceiling(quota, t)
        used_today = account.used
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
                limit=account.limit,
                follows_default=account.follows_default,
                paused_reason=await quota.paused_reason(t.id),
                history=history,
            )
        )
    app = await limits.app_budget(quota)
    global_used = app.used

    history = [
        DayCount(
            date=d.isoformat(),
            count=global_used if d == today else sum(past.get((t.id, d), 0) for t in tenants),
        )
        for d in days
    ]
    rows.sort(key=lambda r: (-r.used_today, r.email))
    return UsageOut(
        usage_date=today.isoformat(),
        global_used=app.used,
        global_limit=app.limit,
        global_remaining=max(0, app.limit - app.used),
        pause_at=app.pause_at,
        until_pause=app.remaining,
        resets_at=app.resets_at,
        ceiling_default=limits.ceiling_default(),
        shops_used=await app_shop_count(session),
        shops_limit=get_settings().max_shops_app_wide,
        history=history,
        tenants=rows,
    )
