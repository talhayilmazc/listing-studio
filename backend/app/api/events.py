"""What a seller met on a page, recorded server side without personal data (``seller_event``).

* Every send summary (the workspace's ``GET /batches/{id}/shops/{shop}/rows`` with the rows
  chosen): how many go and, per reason, how many won't (:func:`record_summary`).
* Every frontend error and failed action the page reports (``POST /api/events``): the page's
  path (no query string), the action (a method and the API path, ids included; no body), and
  a short message with e-mail addresses removed. At most ``PER_MINUTE`` a minute per account.

Read by ``python -m app.cli seller-events <email> --since <time>`` (ops/seller_events.py).
Kept 30 days (workers/retention.py), deleted with the account. Nothing here is shown to
another account.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_redis, get_session
from app.db.models import SellerEvent, Tenant

router = APIRouter(prefix="/api", tags=["events"])

PER_MINUTE = 30
MESSAGE_CHARS = 300
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def clean(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    return _EMAIL.sub("[email]", " ".join(str(text).split()))[:limit] or None


class EventIn(BaseModel):
    kind: Literal["frontend_error", "action_failed"]
    page: str | None = Field(default=None, max_length=2000)
    action: str | None = Field(default=None, max_length=2000)
    message: str | None = Field(default=None, max_length=4000)


@router.post("/events", status_code=204)
async def record_event(
    body: EventIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    redis: Redis = Depends(get_redis),
) -> Response:
    """The page reports an error or a failed action. Over the per-minute limit it is dropped."""
    key = f"seller-events:{tenant.id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count <= PER_MINUTE:
        session.add(SellerEvent(tenant_id=tenant.id, kind=body.kind, page=clean((body.page or "").split("?")[0], 200),
                                action=clean(body.action, 200), message=clean(body.message, MESSAGE_CHARS)))
        await session.commit()
    return Response(status_code=204)


def reason_of(row: Any) -> str:
    """The kind of reason a workspace row won't go (counts only, never its text)."""
    if row.state == "there":
        return "already_in_shop"
    if row.errors:
        return "text_etsy_would_refuse"
    why = (row.reason or "").lower()
    if why.startswith("not approved"):
        return "not_approved"
    if why.startswith("edited for"):
        return "edited_for_shop_approve_again"
    if "image files were deleted" in why:
        return "files_deleted"
    if "production partner" in why:
        return "production_partner_required"
    if "profile" in why or "set up" in why:
        return "shop_or_profile_not_set_up"
    return "other"


async def record_summary(session: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID, shop_id: uuid.UUID,
                         preview: Any) -> None:
    """One send summary's result: how many go, and how many won't per reason."""
    counts: dict[str, int] = {"chosen": len(preview.rows), "going": int(preview.total)}
    for row in preview.rows:
        if row.state != "new":
            counts[reason_of(row)] = counts.get(reason_of(row), 0) + 1
    if preview.refused:
        counts["sku_refused"] = len(preview.refused)
    session.add(SellerEvent(tenant_id=tenant_id, kind="send_summary", page=f"/batches/{batch_id}",
                            action=f"send summary for shop {shop_id}", message=clean(preview.disabled, MESSAGE_CHARS),
                            counts=counts))
    await session.commit()
