"""Account endpoints: invite-only registration, login, logout, password change.

Production-spec A. Sessions are server-side (Redis) and carried in an HttpOnly
cookie; see :mod:`app.core.sessions`. Nothing here logs an email, a password, a
session token or an invite code.
"""

from __future__ import annotations

import secrets
import uuid
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant, get_session, get_session_store
from app.core import audit
from app.core.config import get_settings
from app.core.invites import hash_code, redeemable_by
from app.core.ratelimit import client_ip
from app.core.passwords import (
    WeakPassword,
    generate_temp_password,
    hash_password,
    needs_rehash,
    validate_strength,
    verify_password,
)
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import InviteCode, Tenant, TenantStatus

router = APIRouter(prefix="/api/account", tags=["account"])


# --- Schemas ----------------------------------------------------------------
# A deliberately permissive shape check. Deliverability is proven by the invite
# handed over out of band, not by parsing; a stricter grammar would only add a
# dependency and reject valid addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class _EmailIn(BaseModel):
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not EMAIL_RE.match(cleaned):
            raise ValueError("not a valid email address")
        return cleaned


class RegisterRequest(_EmailIn):
    password: str
    invite_code: str


class LoginRequest(_EmailIn):
    password: str


class ResetRequest(_EmailIn):
    """Admin password reset target."""


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    daily_quota: int
    must_change_password: bool
    # Shows the Admin entry in the UI. Never trusted: /api/admin re-checks it.
    is_admin: bool = False
    # Features an admin turned on for this account (v7 §B); the server re-checks each.
    features: dict[str, bool] = {}


class InviteCreateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=200)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class InviteCreated(BaseModel):
    """The only time the code is ever readable — it is stored hashed."""

    code: str
    expires_at: datetime | None


class TempPasswordOut(BaseModel):
    email: str
    temporary_password: str


# --- Helpers ----------------------------------------------------------------
def _normalise_email(email: str) -> str:
    return email.strip().casefold()


def _client_ip(request: Request) -> str:
    # One resolver for the lockout and the rate limiter. It ignores
    # X-Forwarded-For, whose first entry the client controls — trusting it let
    # an attacker present a fresh "IP" per attempt and never trip the lockout.
    return client_ip(request)


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _out(tenant: Tenant) -> AccountOut:
    return AccountOut(
        id=tenant.id,
        email=tenant.email,
        daily_quota=tenant.daily_quota,
        must_change_password=tenant.must_change_password,
        is_admin=tenant.is_admin,
        features={k: bool(v) for k, v in (tenant.features or {}).items()},
    )


async def _require_bootstrap_token(session: AsyncSession, token: str | None) -> None:
    """ADMIN_TOKEN guard for the bootstrap endpoints, compared in constant time.

    Bootstrap only: once any admin account exists, these endpoints answer 404
    whatever the token, and all administration goes through a signed-in admin's
    session (/api/admin). Unset ADMIN_TOKEN means disabled, not open.
    """
    configured = get_settings().admin_token
    if not configured or not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=404, detail="not found")
    admins = await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.is_admin))
    if admins:
        raise HTTPException(status_code=404, detail="not found")


# --- Registration -----------------------------------------------------------
@router.post("/register", response_model=AccountOut, status_code=201)
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> AccountOut:
    """Create an account against a single-use invite code (A1)."""
    email = _normalise_email(body.email)

    try:
        validate_strength(body.password, email=email)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = await session.execute(
        select(InviteCode).where(InviteCode.code_hash == hash_code(body.invite_code))
    )
    invite = row.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    # One message for every bad-code case — unknown, used, expired, revoked, or
    # bound to another address: never reveal which part was wrong.
    if not redeemable_by(invite, email, now):
        raise HTTPException(status_code=400, detail="invalid or already used invite code")
    assert invite is not None  # narrowed by redeemable_by

    tenant = Tenant(
        email=email,
        password_hash=hash_password(body.password),
        status=TenantStatus.active,
        daily_quota=get_settings().tenant_daily_quota,
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="that email is already registered") from exc

    # Spend the code in the same transaction, so a race cannot redeem it twice.
    invite.used_by_tenant_id = tenant.id
    invite.used_at = now
    await session.commit()
    await session.refresh(tenant)

    _set_session_cookie(response, await sessions.create(tenant.id))
    return _out(tenant)


# --- Login / logout ---------------------------------------------------------
@router.post("/login", response_model=AccountOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> AccountOut:
    """Exchange credentials for a session cookie (A2)."""
    email = _normalise_email(body.email)
    scopes = (f"email:{email}", f"ip:{_client_ip(request)}")

    for scope in scopes:
        if await sessions.is_locked(scope):
            retry = await sessions.retry_after(scope)
            raise HTTPException(
                status_code=429,
                detail="too many attempts; try again later",
                headers={"Retry-After": str(retry or 900)},
            )

    row = await session.execute(select(Tenant).where(func.lower(Tenant.email) == email))
    tenant = row.scalar_one_or_none()

    # Always verify against something so a missing account and a wrong password
    # take comparable time and are indistinguishable from outside.
    stored = tenant.password_hash if tenant else _DUMMY_HASH
    ok = verify_password(stored, body.password) and tenant is not None

    if not ok:
        for scope in scopes:
            await sessions.register_failure(scope)
        raise HTTPException(status_code=401, detail="invalid email or password")

    assert tenant is not None  # narrowed by `ok`
    if tenant.status is not TenantStatus.active:
        raise HTTPException(status_code=403, detail="account suspended")

    for scope in scopes:
        await sessions.clear_failures(scope)

    # Opportunistically upgrade a hash made under weaker parameters.
    if needs_rehash(tenant.password_hash):
        tenant.password_hash = hash_password(body.password)
        await session.commit()

    _set_session_cookie(response, await sessions.create(tenant.id))
    return _out(tenant)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    sessions: SessionStore = Depends(get_session_store),
) -> Response:
    """End this session. Safe to call without one."""
    await sessions.destroy(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=AccountOut)
async def me(tenant: Tenant = Depends(current_tenant)) -> AccountOut:
    """Who this session belongs to. 401 without one."""
    return _out(tenant)


@router.post("/password", response_model=AccountOut)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    tenant: Tenant = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> AccountOut:
    """Replace the password, ending every existing session (A3)."""
    if not verify_password(tenant.password_hash, body.current_password):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    try:
        validate_strength(body.new_password, email=tenant.email)
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tenant.password_hash = hash_password(body.new_password)
    tenant.must_change_password = False
    await session.commit()
    await session.refresh(tenant)

    # Every session dies, including this one; then issue a fresh cookie so the
    # caller who just proved knowledge of the password stays signed in.
    await sessions.destroy_all(tenant.id)
    _set_session_cookie(response, await sessions.create(tenant.id))
    return _out(tenant)


# --- Admin (ADMIN_TOKEN) ----------------------------------------------------
@router.post("/admin/invites", response_model=InviteCreated, status_code=201)
async def create_invite(
    body: InviteCreateRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(get_session),
) -> InviteCreated:
    """Mint a single-use invite code. Returned once; only its hash is stored."""
    await _require_bootstrap_token(session, x_admin_token)

    code = secrets.token_urlsafe(18)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )

    invite = InviteCode(code_hash=hash_code(code), note=body.note, expires_at=expires_at)
    session.add(invite)
    await session.flush()
    audit.record(session, "invite.created", actor=None, target_invite_id=invite.id, via="bootstrap")
    await session.commit()
    return InviteCreated(code=code, expires_at=expires_at)


@router.post("/admin/reset-password", response_model=TempPasswordOut)
async def admin_reset_password(
    body: ResetRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> TempPasswordOut:
    """Issue a temporary password; the tenant must change it at next login (A4)."""
    await _require_bootstrap_token(session, x_admin_token)

    target = _normalise_email(body.email)
    row = await session.execute(select(Tenant).where(func.lower(Tenant.email) == target))
    tenant = row.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="no such account")

    temp = generate_temp_password()
    tenant.password_hash = hash_password(temp)
    tenant.must_change_password = True
    audit.record(
        session, "user.temporary_password", actor=None, target_tenant_id=tenant.id, via="bootstrap"
    )
    await session.commit()
    # Any session opened with the old password is now void.
    await sessions.destroy_all(tenant.id)
    return TempPasswordOut(email=tenant.email, temporary_password=temp)



# A well-formed argon2id hash of a random secret, used to equalise timing when
# no account matches. Computed once at import.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
