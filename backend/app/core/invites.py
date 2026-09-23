"""Invite codes: hashing and state, shared by registration and the admin screens.

One definition of "can this code be redeemed", so the list an admin reads and
the check a registration passes can never disagree.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from app.db.models import InviteCode


class InviteState(str, Enum):
    unused = "unused"
    used = "used"
    expired = "expired"
    revoked = "revoked"


def hash_code(code: str) -> str:
    """Invite codes are high-entropy, so a plain SHA-256 is the right primitive."""
    return hashlib.sha256(code.strip().encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def invite_state(invite: InviteCode, now: datetime | None = None) -> InviteState:
    now = now or datetime.now(timezone.utc)
    if invite.used_by_tenant_id is not None or invite.used_at is not None:
        return InviteState.used
    if invite.revoked_at is not None:
        return InviteState.revoked
    if invite.expires_at is not None and _aware(invite.expires_at) < now:
        return InviteState.expired
    return InviteState.unused


def redeemable_by(invite: InviteCode | None, email: str, now: datetime | None = None) -> bool:
    """Whether ``email`` may register with ``invite``. ``email`` must be normalised."""
    if invite is None or invite_state(invite, now) is not InviteState.unused:
        return False
    return invite.bound_email is None or invite.bound_email == email
