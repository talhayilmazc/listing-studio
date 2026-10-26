"""``python -m app.cli reconcile-etsy <email> [--apply]``: one account's drafts against Etsy.

Each of the account's drafts (the app's records in state "draft", in every connected shop)
is read on Etsy, read only, through the rate limiter and the daily budget as upkeep
(getListingsByListingIds, 100 per request; one not returned is read alone before it counts
as deleted). The report says what would change (``etsy/reconcile.py``):

* published in Etsy by the seller (live there, a draft here): recorded live, its go-live and
  open group-schedule slot close;
* deleted on Etsy: marked so (never deleted), its go-live and open slot close;
* changed on Etsy (inactive, expired, sold out): its scheduled go-live is withdrawn.

The dry run (the default) changes nothing. ``--apply`` changes only our own records, in one
transaction per shop. **Nothing is ever written to Etsy.** Titles are cut at 40 characters;
no token, no buyer data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logsafety import redact
from app.db.models import ConnectionStatus, EtsyConnection, ListingPublication, Tenant
from app.etsy import reconcile

TITLE_CHARS = 40
ORDER = (reconcile.PUBLISHED, reconcile.DELETED, reconcile.WITHDRAWN)


def _when(at: datetime | None) -> str:
    if at is None:
        return "-"
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.strftime("%Y-%m-%d %H:%M UTC")


async def run(
    sm: async_sessionmaker,
    email: str,
    *,
    apply: bool = False,
    client_factory: Callable | None = None,
    token_service: Any = None,
) -> str:
    import httpx

    from app.etsy.api import RateLimitExceeded
    from app.etsy.guard import EtsyCallsBlocked
    from app.ops.draft_failures import _default_client, _default_tokens

    factory = client_factory or _default_client
    tokens = token_service or _default_tokens()
    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
        if tenant is None:
            return f"no account with the address {email}"
        who = (tenant.email, str(tenant.id))
        shops = list((await session.execute(select(EtsyConnection).where(
            EtsyConnection.tenant_id == tenant.id, EtsyConnection.status == ConnectionStatus.active)
            .order_by(EtsyConnection.position, EtsyConnection.connected_at))).scalars())
        shop_ids = [s.id for s in shops]
        names = {s.id: s.shop_name or "a shop" for s in shops}
    lines: list[str] = []
    found: dict[str, list[str]] = defaultdict(list)
    kept = unread = calls = 0
    notes: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as http:
        for shop_id in shop_ids:
            name = names[shop_id]
            try:
                async with sm() as session:
                    shop = await session.get(EtsyConnection, shop_id)
                    drafts = list((await session.execute(select(ListingPublication).where(
                        ListingPublication.connection_id == shop_id, ListingPublication.state == "draft")
                        .order_by(ListingPublication.created_at))).scalars())
                    if not drafts:
                        continue
                    if shop is None or (shop.access_token_enc is None and shop.refresh_token_enc is None):
                        notes.append(f"{name}: not checked (no Etsy key is stored for this shop; it needs reconnecting)")
                        continue
                    token = await tokens.get_valid_access_token(session, shop)  # an expired key is renewed (OAuth)
                    await session.commit()
                    client = factory(shop_id, http)
                    kw = {"access_token": token, "tenant_id": shop.tenant_id}
                    reading = await reconcile.read_states(client, [p.etsy_listing_id for p in drafts], kw,
                                                          singles=len(drafts))
                    calls += reading.requests
                    for publication in drafts:
                        state = reading.states.get(publication.etsy_listing_id)
                        if state is None:
                            unread += 1
                            continue
                        change = reconcile.decide(publication, state)
                        if change is None:
                            kept += 1
                            if apply:
                                publication.etsy_checked_at = datetime.now(timezone.utc)
                            continue
                        found[change].append(str(publication.etsy_listing_id))
                        schedule = f"; its go-live {_when(publication.scheduled_for)} closes" if publication.scheduled_for else ""
                        slots = await reconcile.open_slots(session, publication.content_id, publication.connection_id)
                        if slots:
                            schedule += f"; {len(slots)} group-schedule slot(s) close"
                        title = redact(publication.title or "")[:TITLE_CHARS]
                        lines.append(f"  {name} · {publication.etsy_listing_id} · \"{title}\" · draft here, "
                                     f"{state} on Etsy → {change}{schedule}")
                        if apply:
                            await reconcile.apply(session, publication, state, reading.listings.get(publication.etsy_listing_id))
                    if apply:
                        await session.commit()
                    else:
                        await session.rollback()
            except RateLimitExceeded:
                notes.append("stopped: the app's daily Etsy budget is used up (nothing more was read)")
                break
            except EtsyCallsBlocked:
                notes.append("Etsy calls are blocked here (ETSY_CALLS_BLOCKED): run this on the server")
                break
            except Exception as exc:  # noqa: BLE001 - one shop's trouble does not hide the others
                notes.append(f"{name}: not checked ({type(exc).__name__}: {redact(str(exc))[:160]})")
    mode = ("applied: only our records changed; nothing was written to Etsy" if apply
            else "dry run: nothing changed (--apply changes only our records, never Etsy)")
    out = [f"reconcile-etsy for {who[0]} (account {who[1][:8]}): {mode}",
           f"Etsy read: {calls} request(s), read only, through the rate limiter (upkeep)", "", "== summary"]
    for change in ORDER:
        ids = found.get(change, [])
        out.append(f"  {change}: {len(ids)}" + (f" · {', '.join(ids)}" if ids else ""))
    out.append(f"  as recorded (still a draft on Etsy): {kept}")
    if unread:
        out.append(f"  not read this time: {unread}")
    out += [f"  {n}" for n in notes]
    out += ["", "== changes" + ("" if lines else ": none")] + lines
    return "\n".join(out).rstrip() + "\n"

