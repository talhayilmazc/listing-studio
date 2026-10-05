"""Audit log writer for admin actions and destructive actions.

Every change an admin makes — and every change the server CLI makes — goes
through :func:`record`, in the same transaction as the change itself: if the
change commits, so does its audit row, and if it rolls back, neither remains.

Every destructive action — a seller's or the app's own — goes through
:func:`destructive`, which always names the actor, the account, the shop and
the object by id (STEP 0: rows vanished in production with nothing on record).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Tenant


def record(
    session: AsyncSession,
    action: str,
    *,
    actor: Tenant | None,
    target_tenant_id: uuid.UUID | None = None,
    target_invite_id: uuid.UUID | None = None,
    **details: Any,
) -> AuditLog:
    """Add an audit row to ``session`` (committed with the caller's change).

    ``actor`` is None for the server CLI. ``details`` must not contain email
    addresses, passwords or codes: the log names people by id only.
    """
    row = AuditLog(
        actor_tenant_id=actor.id if actor is not None else None,
        action=action,
        target_tenant_id=target_tenant_id,
        target_invite_id=target_invite_id,
        details=details,
    )
    session.add(row)
    return row


#: Destructive actions, each recorded with :func:`destructive`.
DESTRUCTIVE = frozenset({
    "profile.deleted",
    "batch.deleted",
    "image.deleted",
    "shop.disconnected",
    "publication.deleted_on_etsy",
    "schedule.cancelled",
    "profile.unlinked",
    "profile.merged",
    "shop_group.deleted",
})


def destructive(
    session: AsyncSession,
    action: str,
    *,
    actor: Tenant | None,
    tenant_id: uuid.UUID,
    shop_id: uuid.UUID | None,
    object_id: uuid.UUID | str,
    **details: Any,
) -> AuditLog:
    """Record a destructive action: who did it, to which account, shop and object.

    ``actor`` is the signed-in account, or None when the app did it itself (a
    worker); ``by`` says which. ``shop_id`` is the ``etsy_connection`` id, or
    None for an object that belongs to no shop yet (a batch without one). Ids
    only, never names, titles or other personal data.
    """
    if action not in DESTRUCTIVE:
        raise ValueError(f"unknown destructive action {action!r}")
    return record(
        session,
        action,
        actor=actor,
        target_tenant_id=tenant_id,
        by="system" if actor is None else ("seller" if actor.id == tenant_id else "admin"),
        shop_id=str(shop_id) if shop_id is not None else None,
        object_id=str(object_id),
        **details,
    )
