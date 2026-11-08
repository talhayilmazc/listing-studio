"""The first-run checklist on Home: four steps from a new account to a first listing on Etsy.

1. Connect your shop.  2. Upload your first design.  3. Review and approve.  4. Send or schedule it.

Stored on the server, per account: each step is ticked by its funnel row (pipeline/funnel.py),
written when the seller did the thing, never by a click on the checklist. A shop connected before
the funnel existed ticks step 1 from the shop itself. Each step links straight to where it is done;
steps 3 and 4 open the batch that is waiting for it. The example designs tick nothing: they show how
the app works, they are not the seller's work.

Once every step is done the checklist becomes one "You're set up" note, until the seller dismisses
it (``tenant.setup_note_dismissed_at``). The step texts are here and only here; the page shows them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConnectionStatus, EtsyConnection, GeneratedContent, ListingPublication, PlannedSlot, Tenant, UploadBatch
from app.pipeline import funnel


@dataclass
class Step:
    key: str
    title: str
    body: str
    href: str
    action: str
    done: bool
    done_at: datetime | None


@dataclass
class Checklist:
    steps: list[Step]
    #: The first step not done yet; None when all are.
    next: str | None
    all_done: bool
    #: The seller closed the "You're set up" note.
    dismissed: bool
    #: The account's example batch, if it has one (not deleted).
    example_batch_id: uuid.UUID | None


TEXTS: dict[str, tuple[str, str, str]] = {
    # key: (title, body, button)
    "shop": ("Connect your shop", "Sign in to Etsy as the shop's owner. Nothing is published without you.", "Connect your shop"),
    "upload": ("Upload your first design", "One design file or a few photos of it. A folder or a ZIP works too.", "Upload designs"),
    "approve": ("Review and approve", "Check the title and tags Listyro wrote, change anything, then approve.", "Review"),
    "send": ("Send or schedule it", "Create it as a draft in your shop, or choose when it goes live.", "Send"),
}


async def _waiting_batch(session: AsyncSession, tenant_id: uuid.UUID, *, approved: bool) -> uuid.UUID | None:
    """The newest of the seller's own batches with a listing written and not approved yet
    (``approved=False``), or approved and not sent anywhere yet (``approved=True``)."""
    sent = exists().where(ListingPublication.content_id == GeneratedContent.id)
    planned = exists().where(PlannedSlot.content_id == GeneratedContent.id)
    query = (
        select(UploadBatch.id)
        .join(GeneratedContent, GeneratedContent.batch_id == UploadBatch.id)
        .where(UploadBatch.tenant_id == tenant_id, UploadBatch.deleted_at.is_(None), ~UploadBatch.is_example,
               GeneratedContent.approved.is_(approved))
        .order_by(UploadBatch.created_at.desc())
        .limit(1)
    )
    if approved:
        query = query.where(~sent, ~planned)
    return await session.scalar(query)


async def checklist(session: AsyncSession, tenant: Tenant) -> Checklist:
    did = await funnel.done(session, tenant.id)
    shops = await session.scalar(
        select(func.count()).select_from(EtsyConnection)
        .where(EtsyConnection.tenant_id == tenant.id, EtsyConnection.status == ConnectionStatus.active)
    )
    to_review = await _waiting_batch(session, tenant.id, approved=False)
    to_send = await _waiting_batch(session, tenant.id, approved=True)
    example = await session.scalar(
        select(UploadBatch.id).where(UploadBatch.tenant_id == tenant.id, UploadBatch.is_example,
                                     UploadBatch.deleted_at.is_(None))
    )
    done = {
        "shop": (funnel.SHOP_CONNECTED in did or bool(shops), did.get(funnel.SHOP_CONNECTED)),
        "upload": (funnel.FIRST_UPLOAD in did, did.get(funnel.FIRST_UPLOAD)),
        "approve": (funnel.FIRST_APPROVED in did, did.get(funnel.FIRST_APPROVED)),
        "send": (funnel.FIRST_SENT in did, did.get(funnel.FIRST_SENT)),
    }
    hrefs = {
        "shop": "/connect",
        "upload": "/upload",
        "approve": f"/batches/{to_review}/review" if to_review else "/review",
        "send": f"/batches/{to_send}/review#send" if to_send else "/review",
    }
    steps = [
        Step(key=key, title=TEXTS[key][0], body=TEXTS[key][1], href=hrefs[key], action=TEXTS[key][2],
             done=done[key][0], done_at=done[key][1])
        for key in ("shop", "upload", "approve", "send")
    ]
    left = [s.key for s in steps if not s.done]
    return Checklist(
        steps=steps, next=left[0] if left else None, all_done=not left,
        dismissed=tenant.setup_note_dismissed_at is not None, example_batch_id=example,
    )
