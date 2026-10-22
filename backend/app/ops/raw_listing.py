"""``python -m app.cli etsy-raw-listing``: what Etsy really returns for one of a seller's listings.

For the operator, on the server: Etsy's OpenAPI document may lag behind the live API, so this
prints the raw JSON of a listing as its shop's owner sees it, every field name and value, and
then lists any field whose name looks like an answer to Etsy's Creativity Standards questions
("How does your shop produce this item?", "What tools are used to make this item?").

READ ONLY toward Etsy: three GET requests (getListing with its associations, the listing's
properties and its inventory), each through the app's bucket and counted in the app's daily
budget as upkeep (never a seller's ceiling). The listing is found by Etsy shop id or the app's
shop id and a listing id, or by a profile's name (its main shop and reference listing, read in
a read-only transaction). Nothing is written to the database except a token refresh when the
shop's access token has expired (that is OAuth, not Etsy content). No buyer data is requested:
getListing's "User" association is not asked for.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import ConnectionStatus, EtsyConnection, ListingProfile

#: getListing associations asked for (Etsy's list, less "User" and "BuyerPrice").
INCLUDES = ["Images", "Shop", "Personalization", "Translations", "Videos"]
#: A field name that might carry the Creativity Standards answers.
CREATIVITY = re.compile(r"produce|production|how_made|made_by|made_from|tool|craft|creativ|maker|machine|handmade|technique|method", re.I)
#: The Creativity fields Etsy has had for years, copied from the reference on every draft already:
#: listed apart, so a new field stands out.
KNOWN = {"who_made", "when_made", "is_supply", "production_partners", "production_partner_ids", "production_partner_id"}


def field_names(value: Any, prefix: str = "") -> list[str]:
    """Every field name in a JSON value, with its path ("results[].property_name")."""
    out: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out.append(path)
            out += field_names(inner, path)
    elif isinstance(value, list):
        for inner in value:
            out += field_names(inner, f"{prefix}[]")
    return list(dict.fromkeys(out))


def creativity_candidates(responses: dict[str, Any]) -> list[str]:
    """Field names, and property names (properties carry their name as a value), that look
    like an answer to "how is it produced" or "what tools"."""
    hits: list[str] = []
    for name, body in responses.items():
        for path in field_names(body):
            leaf = path.rsplit(".", 1)[-1].replace("[]", "")
            if CREATIVITY.search(leaf) and leaf not in KNOWN and not path.split(".")[0].startswith("production_partners"):
                hits.append(f"{name}: {path}")
        for prop in (body or {}).get("results") or [] if isinstance(body, dict) else []:
            if isinstance(prop, dict) and CREATIVITY.search(str(prop.get("property_name") or "")):
                hits.append(f"{name}: property {prop.get('property_name')!r} = {prop.get('values')!r}")
    return hits


async def _find(sm: async_sessionmaker, shop: str | None, listing: str | None, profile: str | None) -> tuple[EtsyConnection | None, int | None, str]:
    """(shop, listing id, how it was found) from the arguments, in a read-only transaction."""
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        if profile is not None:
            rows = list((await session.execute(select(ListingProfile).where(
                func.lower(ListingProfile.name) == profile.strip().lower()))).scalars())
            if not rows:
                # Close names (a word of it in the name), with their main shop: "Regular" in PCS, say.
                words = [w for w in re.split(r"[^\w]+", profile.lower()) if len(w) > 2]
                near = [p for p in (await session.execute(select(ListingProfile))).scalars()
                        if any(w in p.name.lower() for w in words)][:20]
                shops = {c.id: c for c in (await session.execute(select(EtsyConnection).where(
                    EtsyConnection.id.in_([p.connection_id for p in near])))).scalars()} if near else {}
                listed = "".join(
                    f"\n  {p.name!r}  main shop {shops[p.connection_id].shop_name if p.connection_id in shops else p.connection_id}"
                    f"  reference listing {p.reference_listing_id}" for p in near)
                return None, None, f"no profile named {profile!r}" + (f"; close names:{listed}" if near else "")
            if len(rows) > 1:
                listed = "\n".join(f"  profile {p.id}  main shop {p.connection_id}  reference listing {p.reference_listing_id}" for p in rows)
                return None, None, f"{len(rows)} profiles are named {profile!r}; run again with a shop id and listing id:\n{listed}"
            found = rows[0]
            if found.reference_listing_id is None:
                return None, None, f"profile {profile!r} has no reference listing"
            connection = await session.get(EtsyConnection, found.connection_id)
            return connection, int(found.reference_listing_id), f"profile {found.name!r} ({found.id}): main shop and reference listing"
        if shop is None or listing is None or not listing.isdigit():
            return None, None, "give <shop_id> <listing_id>, or --profile NAME"
        connection = None
        try:
            connection = await session.get(EtsyConnection, uuid.UUID(shop))
        except ValueError:
            if shop.isdigit():
                connection = (await session.execute(select(EtsyConnection).where(
                    EtsyConnection.shop_id == int(shop), EtsyConnection.status == ConnectionStatus.active))).scalars().first()
        return connection, int(listing), "the shop and listing given"


async def raw_listing(
    sm: async_sessionmaker,
    shop: str | None,
    listing: str | None,
    *,
    profile: str | None = None,
    client_factory: Callable | None = None,
    token_service: Any = None,
) -> str:
    import httpx

    from app.cli import _client_for_check, _token_service

    connection, listing_id, how = await _find(sm, shop, listing, profile)
    if connection is None or listing_id is None:
        return f"{how}; no request made" if "no request" not in how else how
    if connection.status is not ConnectionStatus.active or connection.shop_id is None:
        return f"shop {connection.id} is not connected (or its Etsy shop id is unknown); no request made"
    async with sm() as session:
        connection = await session.get(EtsyConnection, connection.id)
        token = await (token_service or _token_service()).get_valid_access_token(session, connection)
    kw = {"access_token": token, "tenant_id": connection.tenant_id}
    responses: dict[str, Any] = {}
    failures: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30.0) as http:
        client = (client_factory or _client_for_check)(connection.id, http)
        calls = {
            f"getListing (includes={','.join(INCLUDES)})": lambda: client.get_listing(listing_id, includes=INCLUDES, **kw),
            "getListingProperties": lambda: client.get_listing_properties(connection.shop_id, listing_id, **kw),
            "getListingInventory": lambda: client.get_listing_inventory(listing_id, **kw),
        }
        for name, call in calls.items():
            try:
                responses[name] = await call()
            except Exception as exc:  # noqa: BLE001 - say which request failed and go on to the next
                failures[name] = f"{type(exc).__name__}: {str(exc)[:300]}"
    lines = [
        f"shop: {connection.id} (Etsy shop {connection.shop_id}, {connection.shop_name or 'no name'})",
        f"listing: {listing_id}  (found by {how})",
        f"Etsy requests made: {len(calls)} (read only; counted in the app's daily budget as upkeep)",
        "",
    ]
    for name, body in responses.items():
        lines += [f"=== {name}: field names", *[f"  {p}" for p in field_names(body)], f"=== {name}: raw JSON",
                  json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True), ""]
    for name, why in failures.items():
        lines.append(f"=== {name}: failed: {why}")
    hits = creativity_candidates(responses)
    listing_body = next(iter(responses.values()), {}) if responses else {}
    known = {k: listing_body.get(k) for k in sorted(KNOWN) if isinstance(listing_body, dict) and k in listing_body}
    lines += ["", f"=== The Creativity fields already copied from the reference: {json.dumps(known, ensure_ascii=False)}"]
    lines += ["=== Other fields that might answer \"How does your shop produce this item?\" / \"What tools are used to make this item?\":"]
    lines += [f"  {h}" for h in hits] if hits else ["  none"]
    return "\n".join(lines)
