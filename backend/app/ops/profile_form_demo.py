"""Sample options for the profile form in the demo account (``seed-demo``; never in production).

The profile form offers a shop's own Etsy settings and Etsy's categories, read through the queue. A development
machine never calls Etsy, so the demo account's shops get made-up lists here: enough to lay the form out, make a
profile from scratch, copy one to a shop that has no production partner, and run the browser tests. Nothing in
it is a real shop's. The ids are invented; the names are ordinary words.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import EtsyConnection, Tenant

#: Kept a week in the demo (an hour and a day in real use): a demo is looked at for longer than that.
TTL_SECONDS = 7 * 24 * 3600

NODES: dict[str, Any] = {"results": [
    {"id": 9001, "name": "Clothing", "children": [
        {"id": 9002, "name": "Gender-Neutral Adult Clothing", "children": [
            {"id": 9003, "name": "Tops & Tees", "children": [{"id": 9004, "name": "T-shirts", "children": []},
                                                              {"id": 9005, "name": "Tank Tops", "children": []}]},
            {"id": 9006, "name": "Hoodies & Sweatshirts", "children": [{"id": 9007, "name": "Hoodies", "children": []},
                                                                       {"id": 9008, "name": "Sweatshirts", "children": []}]}]},
        {"id": 9010, "name": "Kids' Clothing", "children": [{"id": 9011, "name": "T-shirts", "children": []}]}]},
    {"id": 9020, "name": "Home & Living", "children": [
        {"id": 9021, "name": "Kitchen & Dining", "children": [{"id": 9022, "name": "Mugs", "children": []}]}]},
    {"id": 9030, "name": "Bags & Purses", "children": [{"id": 9031, "name": "Tote Bags", "children": []}]},
]}


def _values(names: list[str], start: int) -> list[dict[str, Any]]:
    return [{"value_id": start + i, "name": name} for i, name in enumerate(names)]


PROPS: dict[str, Any] = {"results": [
    {"property_id": 9200, "name": "Primary color", "is_required": False, "supports_attributes": True, "is_multivalued": False,
     "possible_values": _values(["Black", "White", "Blue", "Green", "Red", "Pink", "Gray", "Beige"], 1), "scales": []},
    {"property_id": 9201, "name": "Sleeve length", "is_required": False, "supports_attributes": True, "is_multivalued": False,
     "possible_values": _values(["Short sleeve", "Long sleeve", "Sleeveless"], 20), "scales": []},
    {"property_id": 9202, "name": "Neckline", "is_required": False, "supports_attributes": True, "is_multivalued": False,
     "possible_values": _values(["Crew neck", "V-neck", "Scoop neck"], 30), "scales": []},
    {"property_id": 9203, "name": "Occasion", "is_required": False, "supports_attributes": True, "is_multivalued": False,
     "possible_values": _values(["Birthday", "Wedding", "Graduation"], 40), "scales": []},
]}


def lists_for(index: int, empty: bool) -> dict[str, Any]:
    """One demo shop's own lists. ``empty``: a new shop: its own shipping and processing profile, a return policy
    unlike the others' and no production partner."""
    base = 100_000 * (index + 1)
    shipping = [{"shipping_profile_id": base + 1, "title": "Standard shipping", "profile_type": "manual"},
                {"shipping_profile_id": base + 2, "title": "Free shipping", "profile_type": "manual"}]
    returns = ([{"return_policy_id": base + 12, "accepts_returns": False, "accepts_exchanges": False}] if empty else
               [{"return_policy_id": base + 11, "accepts_returns": True, "accepts_exchanges": True, "return_deadline": 30},
                {"return_policy_id": base + 12, "accepts_returns": False, "accepts_exchanges": False}])
    readiness = [{"readiness_state_id": base + 21, "readiness_state": "made_to_order", "min_processing_days": 1,
                  "max_processing_days": 3}]
    partners = [] if empty else [{"production_partner_id": base + 31, "partner_name": "Sample Print Co"}]
    return {"shipping": shipping, "returns": returns, "readiness": readiness, "partners": partners, "currency": "USD"}


async def seed(sm: async_sessionmaker, redis: Any, email: str) -> int:
    """The demo account's shops get their lists (the last shop is the "new, empty" one), and Etsy's categories a
    sample tree. Returns the shops written."""
    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == email))).scalar_one_or_none()
        if tenant is None:
            return 0
        shops = list((await session.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id)
                                            .order_by(EtsyConnection.connected_at, EtsyConnection.id))).scalars())
    for index, shop in enumerate(shops):
        empty = len(shops) > 1 and index == len(shops) - 1
        await redis.set(f"shop-settings:{shop.id}", json.dumps(lists_for(index, empty)), ex=TTL_SECONDS)
    await redis.set("etsy:taxonomy:nodes", json.dumps(NODES), ex=TTL_SECONDS)
    for node in (9004, 9005, 9007, 9008, 9011, 9022, 9031):
        await redis.set(f"etsy:taxonomy:props:{node}", json.dumps(PROPS), ex=TTL_SECONDS)
    return len(shops)
