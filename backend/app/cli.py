"""Server CLI: grant and withdraw the admin role.

The only way anyone becomes an admin — no HTTP endpoint can set the flag. Run it
on the server, inside the API container:

    docker compose -f docker-compose.prod.yml exec api python -m app.cli create-admin you@example.com
    docker compose -f docker-compose.prod.yml exec api python -m app.cli demote-admin someone@example.com
    docker compose -f docker-compose.prod.yml exec api python -m app.cli list-admins

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
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import audit
from app.core.config import get_settings
from app.core.passwords import WeakPassword, hash_password, validate_strength
from app.db.models import Tenant, TenantStatus

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
            daily_quota=get_settings().tenant_daily_quota,
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
    args = parser.parse_args(argv)

    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    if args.command == "create-admin":
        print(asyncio.run(create_admin(sm, args.email, ask_password=prompt_password,
                                       set_password=args.set_password)))
    elif args.command == "demote-admin":
        print(asyncio.run(demote_admin(sm, args.email)))
    else:
        admins = asyncio.run(list_admins(sm))
        print("\n".join(admins) if admins else "no admins yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
