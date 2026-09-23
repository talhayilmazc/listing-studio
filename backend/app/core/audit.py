"""Audit log writer for admin actions.

Every change an admin makes — and every change the server CLI makes — goes
through :func:`record`, in the same transaction as the change itself: if the
change commits, so does its audit row, and if it rolls back, neither remains.
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
