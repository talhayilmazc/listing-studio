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
                       there (pipeline/unfinished.py, the seller's list on Overview);
* a shop to connect    Etsy no longer accepts its sign-in;
* a run that will fail what the check an hour before a scheduled run found, with the fix
                       (workers/preflight.py).

The caller's own account only; from our own database and Redis (no Etsy call).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, func, select
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_redis, get_session
from app.etsy.shops import lost_shops
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


async def attention(session: AsyncSession, tenant: Tenant, redis: Redis | None = None) -> AttentionOut:
    from app.monitor.rules import preflight_results
    from app.pipeline import batch_status, unfinished

    tid = tenant.id
    to_check = int(await session.scalar(select(func.count()).select_from(MockupPhoto).where(
        MockupPhoto.tenant_id == tid, MockupPhoto.status == "ready", MockupPhoto.confirmed_at.is_(None))) or 0)
    colours = int(await session.scalar(select(func.count()).select_from(MockupPhoto).where(
        MockupPhoto.tenant_id == tid, MockupPhoto.status == "ready", MockupPhoto.colour_source == "uncertain")) or 0)
    # Settings carries a badge only for what is broken there (fix/onboarding-followups, Part C): a profile that
    # can no longer write (its reference listing gone, the shop's access lost, its main shop disconnected), or a
    # shop setting Etsy refused to copy. A link still being prepared ("incomplete") is not broken: setup is
    # automatic since 21 October 2026. A refresh that failed for a passing reason is not either.
    from app.workers.profiles import TRANSIENT

    cannot_write = int(await session.scalar(select(func.count()).select_from(ListingProfile).where(
        ListingProfile.tenant_id == tid, ListingProfile.confirmed.is_(True),
        or_(ListingProfile.reference_listing_id.is_(None),
            and_(ListingProfile.refresh_error.is_not(None), ListingProfile.refresh_error != TRANSIENT)))) or 0)
    refused = int(await session.scalar(
        select(func.count()).select_from(ProfileShopLink).join(ListingProfile, ListingProfile.id == ProfileShopLink.profile_id)
        .where(ProfileShopLink.tenant_id == tid, ListingProfile.confirmed.is_(True),
               ProfileShopLink.connection_id != ListingProfile.connection_id, ProfileShopLink.status == "error")) or 0)
    drafted = select(ListingPublication.content_id).where(ListingPublication.tenant_id == tid,
                                                          ListingPublication.state != "deleted_on_etsy")
    approve = int(await session.scalar(select(func.count()).select_from(GeneratedContent).where(
        GeneratedContent.tenant_id == tid, GeneratedContent.approved.is_(False),
        GeneratedContent.id.not_in(drafted),
        GeneratedContent.batch_id.not_in(select(UploadBatch.id).where(
            UploadBatch.tenant_id == tid, UploadBatch.deleted_at.is_not(None))))) or 0)
    batches = list((await session.execute(select(UploadBatch).where(
        UploadBatch.tenant_id == tid, UploadBatch.deleted_at.is_(None)))).scalars())
    statuses = await batch_status.batch_statuses(session, tid, batches)
    failed = sum(1 for s in statuses.values() if "failed" in s)
    records = [r for r in await unfinished.records(session, tid, with_blocks=False)
               if r.etsy_listing_id is not None and r.category in unfinished.SELLER and not r.dismissed]

    # ``nav``: the sidebar entry whose badge counts it, or "" for no badge. Badges are for what needs the seller
    # now; mockup photos to check are listed on Home without one (they matter only when making mockups).
    items = [
        AttentionItem(key="profiles", label=_plural(cannot_write, "profile can no longer write listings: open it to fix",
                                                    "profiles can no longer write listings: open them to fix"),
                      count=cannot_write, href="/profiles", nav="/settings"),
        AttentionItem(key="refused", label=_plural(refused, "shop setting Etsy refused to copy for a profile",
                                                   "shop settings Etsy refused to copy for a profile"),
                      count=refused, href="/profiles", nav="/settings"),
        AttentionItem(key="approve", label=_plural(approve, "listing to approve", "listings to approve"), count=approve,
                      href="/review", nav="/review"),
        AttentionItem(key="failed", label=_plural(failed, "batch with a failed draft", "batches with a failed draft"),
                      count=failed, href="/batches?status=failed", nav="/review"),
        AttentionItem(key="unfinished", label=_plural(len(records), "draft on Etsy to finish or delete", "drafts on Etsy to finish or delete"),
                      count=len(records), href="/dashboard#unfinished-drafts", nav="/dashboard"),
        AttentionItem(key="photos", label=_plural(to_check, "mockup photo to check", "mockup photos to check"), count=to_check,
                      href="/templates", nav=""),
        AttentionItem(key="colours", label=_plural(colours, "photo colour to choose (light or dark)",
                                                   "photo colours to choose (light or dark)"),
                      count=colours, href="/templates", nav=""),
    ]
    lost = await lost_shops(session, tid)
    items += [AttentionItem(key=f"lost:{shop.id}", count=1, href="/connect", nav="/connect", label=(
        f"Etsy no longer accepts the sign-in of {shop.display_name or shop.shop_name or 'a shop'}. Connect it again."))
        for shop in lost]
    if redis is not None:
        # The check an hour before a scheduled run (workers/preflight.py). A shop already said to be lost is said once.
        said = {str(shop.id) for shop in lost}
        items += [AttentionItem(key=f"run:{p['shop']}:{p['kind']}", label=p["line"], count=p["count"], href=p["href"], nav="/scheduled")
                  for p in await preflight_results(redis, tid) if not (p["kind"] == "token" and p["shop"] in said)]
    items = [i for i in items if i.count > 0]
    nav: dict[str, int] = {}
    for i in items:
        if i.nav:
            nav[i.nav] = nav.get(i.nav, 0) + i.count
    return AttentionOut(items=items, total=sum(i.count for i in items), nav=nav)


@router.get("/attention", response_model=AttentionOut)
async def get_attention(session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                        redis: Redis = Depends(get_redis)) -> AttentionOut:
    return await attention(session, tenant, redis)
