"""Tenant ownership guards for queued work (production-spec B5).

A job carries the tenant it was queued for. Every row it then loads by id must
belong to that tenant, otherwise the job is refusing to run rather than acting
on someone else's data. The API already checks ownership when a job is created;
this is the second line, so a malformed or tampered payload cannot cross the
boundary later.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

T = TypeVar("T")


class CrossTenantJob(Exception):
    """A job referenced a row belonging to a different tenant."""


def owned(row: T | None, tenant_id: uuid.UUID, what: str) -> T:
    """Return ``row`` when it exists and belongs to ``tenant_id``, else raise.

    Raises :class:`CrossTenantJob`, which the worker records as a failed job.
    """
    if row is None:
        raise CrossTenantJob(f"{what} not found")
    owner: Any = getattr(row, "tenant_id", None)
    if owner is None or owner != tenant_id:
        # Deliberately vague: the message reaches job.last_error, which the
        # owning tenant can read. It must not confirm the row exists elsewhere.
        raise CrossTenantJob(f"{what} not found")
    return row


def owned_optional(row: T | None, tenant_id: uuid.UUID) -> T | None:
    """Like :func:`owned` but tolerates a missing row; a foreign one still raises."""
    if row is None:
        return None
    owner: Any = getattr(row, "tenant_id", None)
    if owner is None or owner != tenant_id:
        raise CrossTenantJob("referenced row not found")
    return row


# Failures whose messages we wrote ourselves and are safe to show the tenant.
# Anything else — a database error, a filesystem path, a library's internals —
# is replaced by a generic line; the full trace is already in the worker log.
def public_error(exc: BaseException) -> str:
    """The text stored in ``job.last_error``, which the owning tenant can read."""
    import httpx

    from app.core.logsafety import redact
    from app.etsy.api import RateLimitExceeded
    from app.etsy.errors import EtsyError, explain
    from app.pipeline.images import ImageProcessingError

    if isinstance(exc, RateLimitExceeded):
        # Jobs start only when their whole budget fits, and drafts wait for the
        # reset instead of failing (workers/recovery.py); this is for the rest.
        return (
            "Etsy's daily request limit ran out partway through, so this stopped before "
            "finishing. Try again after 00:00 UTC."
        )
    if isinstance(exc, EtsyError):
        # Which step, and Etsy's own reason: about the seller's own listing, shown to them only.
        return redact(explain(exc))[:500]
    if isinstance(exc, httpx.TimeoutException):
        return "Etsy did not answer in time. Nothing is wrong with the listing: try again."
    if isinstance(exc, httpx.TransportError):
        return "The connection to Etsy dropped. Nothing is wrong with the listing: try again."
    from app.etsy.publisher import PublishBlocked

    if isinstance(exc, PublishBlocked):
        # Our own sentence: the listing isn't approved any more, or a compliance finding blocks it.
        return f"Not published: {exc}. Review the listing, then try again."
    if isinstance(exc, (CrossTenantJob, ImageProcessingError, ValueError)):
        return redact(str(exc))[:500] or "the job failed"
    return "the job failed unexpectedly; it has been logged for investigation"
