"""What needs the seller now, counted once for the sidebar badges and Overview's
"Needs your attention" block (``GET /api/attention``).

Each item says what it is, how many, and where to go:

* photos to check      template photos not confirmed yet ("Needs your check"), and photos
                       whose light/dark colour group is uncertain (pipeline/variants.py);
* shops to set up      a confirmed profile not ready in a shop it is used in (something to
                       choose or create, or a setup that failed; profile_shop_link);
* listings to approve  written listings not approved yet, with nothing on Etsy;
* failed drafts        listings whose draft or go-live failed in a shop (the Batches
                       "Failed" status, pipeline/batch_status.py);
* unfinished drafts    drafts created on Etsy and not finished, and extra drafts to delete
                       there (pipeline/unfinished.py, the seller's list on Overview).

The caller's own account only; counts only, from our own database (no Etsy call).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.db.models import (
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    MockupPhoto,
    ProfileShopLink,
    Tenant,
    UploadBatch,
)

router = APIRouter(prefix="/api", tags=["attention"])


class AttentionItem(BaseModel):
    key: str
    label: str
    count: int
    href: str
    #: The sidebar entry that carries this item's count.
    nav: str


class AttentionOut(BaseModel):
    items: list[AttentionItem] = Field(default_factory=list)
    total: int = 0
    #: Sidebar entry (its href) -> the count on its badge.
    nav: dict[str, int] = Field(default_factory=dict)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


async def attention(session: AsyncSession, tenant: Tenant) -> AttentionOut:
    from app.pipeline import batch_status, unfinished

    tid = tenant.id
    to_check = int(await session.scalar(select(func.count()).select_from(MockupPhoto).where(
        MockupPhoto.tenant_id == tid, MockupPhoto.status == "ready", MockupPhoto.confirmed_at.is_(None))) or 0)
    colours = int(await session.scalar(select(func.count()).select_from(MockupPhoto).where(
        MockupPhoto.tenant_id == tid, MockupPhoto.status == "ready", MockupPhoto.colour_source == "uncertain")) or 0)
    setup = int(await session.scalar(
        select(func.count()).select_from(ProfileShopLink).join(ListingProfile, ListingProfile.id == ProfileShopLink.profile_id)
        .where(ProfileShopLink.tenant_id == tid, ListingProfile.confirmed.is_(True),
               ProfileShopLink.connection_id != ListingProfile.connection_id,
               ProfileShopLink.status.in_(("incomplete", "error")))) or 0)
    drafted = select(ListingPublication.content_id).where(ListingPublication.tenant_id == tid,
                                                          ListingPublication.state != "deleted_on_etsy")
    approve = int(await session.scalar(select(func.count()).select_from(GeneratedContent).where(
        GeneratedContent.tenant_id == tid, GeneratedContent.approved.is_(False),
        GeneratedContent.id.not_in(drafted))) or 0)
    batches = list((await session.execute(select(UploadBatch).where(UploadBatch.tenant_id == tid))).scalars())
    statuses = await batch_status.batch_statuses(session, tid, batches)
    failed = sum(1 for s in statuses.values() if "failed" in s)
    from app.api.finish_in_etsy import count as finish_count

    finish = await finish_count(session, tid)
    records = [r for r in await unfinished.records(session, tid, with_blocks=False)
               if r.etsy_listing_id is not None and r.category in unfinished.SELLER and not r.dismissed]

    items = [
        AttentionItem(key="photos", label=_plural(to_check, "photo to check", "photos to check"), count=to_check,
                      href="/templates", nav="/templates"),
        AttentionItem(key="colours", label=_plural(colours, "photo colour to choose (light or dark)",
                                                   "photo colours to choose (light or dark)"),
                      count=colours, href="/templates", nav="/templates"),
        AttentionItem(key="setup", label=_plural(setup, "shop to set up for a profile", "shops to set up for a profile"),
                      count=setup, href="/profiles", nav="/profiles"),
        AttentionItem(key="approve", label=_plural(approve, "listing to approve", "listings to approve"), count=approve,
                      href="/batches?status=in_review", nav="/batches"),
        AttentionItem(key="failed", label=_plural(failed, "batch with a failed draft", "batches with a failed draft"),
                      count=failed, href="/batches?status=failed", nav="/batches"),
        AttentionItem(key="finish", label=_plural(finish, "listing to finish in Etsy (How it's made)",
                                                  "listings to finish in Etsy (How it's made)"),
                      count=finish, href="/batches/finish", nav="/batches/finish"),
        AttentionItem(key="unfinished", label=_plural(len(records), "draft on Etsy to finish or delete", "drafts on Etsy to finish or delete"),
                      count=len(records), href="/dashboard#unfinished-drafts", nav="/dashboard"),
    ]
    items = [i for i in items if i.count > 0]
    nav: dict[str, int] = {}
    for i in items:
        nav[i.nav] = nav.get(i.nav, 0) + i.count
    return AttentionOut(items=items, total=sum(i.count for i in items), nav=nav)


@router.get("/attention", response_model=AttentionOut)
async def get_attention(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> AttentionOut:
    return await attention(session, tenant)
