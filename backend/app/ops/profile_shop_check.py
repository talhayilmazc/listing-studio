"""``python -m app.cli profile-shop-check <email>``: one account's profiles against its
shops, read only.

For each profile and each connected shop: whether the profile is set up there and the
shop-scoped ids a draft there is built with (shipping profile, return policy,
processing profile, production partners; the section is chosen per draft from that
shop's own sections), or "missing" with why. For each shop: its token state (valid,
expired or revoked: never a token). Then the half-made drafts (pipeline/unfinished.py):
listings created on Etsy whose draft stopped, with the shop, the step and Etsy's reason.

Only SELECTs: on Postgres the transaction is opened READ ONLY, so a write could not
happen even by mistake, and it is rolled back at the end. No Etsy call, no token
decrypted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ConnectionStatus, EtsyConnection, ListingProfile, ProfileShopLink, Tenant
from app.pipeline import links as L
from app.pipeline import profile_shops, unfinished


def token_state(shop: EtsyConnection, now: datetime | None = None) -> str:
    """valid / expired / revoked, from the connection's own record (no token is read)."""
    now = now or datetime.now(timezone.utc)
    if shop.status is ConnectionStatus.revoked:
        return "revoked (the shop was disconnected)"
    if shop.status is ConnectionStatus.expired or shop.refresh_token_enc is None:
        return "expired (the seller must reconnect the shop)"
    expires = shop.token_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is None:
        return "valid (access token renews when used)"
    when = expires.strftime("%Y-%m-%d %H:%M UTC")
    return f"valid (access token {'expired' if expires < now else 'good until'} {when}; it renews when used)"


def _ids(values: dict, payload: dict, own: bool) -> list[str]:
    rows = []
    for resource in L.RESOURCES:
        key = L.PAYLOAD_KEY[resource]
        need = L.wanted(payload, resource)
        value = values.get(key)
        if need in (None, "", []):
            shown = "not needed (the reference has none)"
        elif value in (None, "", []):
            shown = "missing"
        else:
            shown = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        rows.append(f"{L.LABEL[resource].lower()}: {shown}")
    if own:
        offering = L.offering_readiness(payload)
        if offering:
            rows.append("processing profile on the inventory's offerings: " + ", ".join(str(i) for i in offering))
    rows.append("section: chosen per draft from this shop's own sections")
    return rows


async def report(sm: async_sessionmaker, email: str) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            return await _report(session, email)
        finally:
            await session.rollback()


async def _report(session: AsyncSession, email: str) -> str:
    tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
    if tenant is None:
        return f"no account with the address {email}"
    shops = list((await session.execute(
        select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id).order_by(EtsyConnection.position)
    )).scalars())
    name = {s.id: s.shop_name or f"shop {s.shop_id}" for s in shops}
    lines = [f"Account {tenant.email} ({str(tenant.id)[:8]})", "", "Shops:"]
    for s in shops:
        lines.append(f"  {name[s.id]} (Etsy shop {s.shop_id}, {s.status.value}): token {token_state(s)}")
    profiles = list((await session.execute(
        select(ListingProfile).where(ListingProfile.tenant_id == tenant.id).order_by(ListingProfile.name)
    )).scalars())
    links = await profile_shops.links_of(session, [p.id for p in profiles])
    lines += ["", "Profiles:"]
    for p in profiles:
        payload = p.cached_payload or {}
        read = p.updated_at.strftime("%Y-%m-%d %H:%M UTC") if p.updated_at else "never"
        lines.append(f'  "{p.name}" ({"confirmed" if p.confirmed else "not confirmed"}): main shop '
                     f"{name.get(p.connection_id, '?')}, reference listing {p.reference_listing_id}, read {read}")
        by_shop: dict = {link.connection_id: link for link in links.get(p.id, [])}
        for s in shops:
            if s.status is not ConnectionStatus.active:
                continue
            if s.id == p.connection_id:
                lines.append(f"    {name[s.id]} (main shop): " + "; ".join(_ids(payload, payload, own=True)))
                continue
            link: ProfileShopLink | None = by_shop.get(s.id)
            if link is None:
                lines.append(f"    {name[s.id]}: not set up")
                continue
            state = profile_shops.state_of(p, link)
            status = "ready" if state is not None and state.ready else (state.status if state else link.status)
            lines.append(f"    {name[s.id]}: link {status}; " + "; ".join(_ids(L.link_values(link), payload, own=False)))
            if state is not None and not state.ready and state.reason:
                lines.append(f"      why: {state.reason}")
    drafts = await unfinished.half_made(session, tenant.id, with_blocks=True)
    lines += ["", f"Half-made drafts: {len(drafts)}"]
    for d in drafts:
        when = d.failed_at.strftime("%Y-%m-%d %H:%M UTC") if d.failed_at else "not failed (still running or never ran again)"
        lines.append(f'  listing {d.etsy_listing_id} in {d.shop_name}: "{d.title[:70]}"')
        lines.append(f"    failed step: {d.step or 'unknown'}; when: {when}")
        lines.append(f"    Etsy said: {d.error or 'no error recorded'}")
        if d.blocked:
            lines.append(f"    cannot be finished yet: {d.blocked}")
        elif d.finishing:
            lines.append("    being finished now")
        else:
            lines.append("    can be finished: the seller presses Finish these drafts")
    return "\n".join(lines)
