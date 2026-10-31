"""The load stack's synthetic world (docker-compose.load.yml). Never elsewhere.

LOAD_ACCOUNTS accounts × LOAD_SHOPS shops × LOAD_LISTINGS listings per shop, with a history (see
``scripts/load_test.py::_account``), tokens the stub accepts, profiles a draft can really use (a
reference payload, each other shop's own ids) and a few drafts the driver schedules to go live.
Refuses to run unless the database is the stack's own. Run after ``alembic upgrade head``.
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.crypto import get_cipher  # noqa: E402
from app.core.terms import TERMS_VERSION  # noqa: E402
from app.pipeline.reference import PAYLOAD_VERSION  # noqa: E402
from scripts.load_test import NOW, Rows, _account  # noqa: E402

REFERENCE: dict[str, Any] = {
    "title": "Reference Tee", "description": "Reference title\n\nA soft cotton tee, printed to order.",
    "taxonomy_id": 2078, "price": 25.99, "who_made": "i_did", "when_made": "made_to_order", "is_supply": False,
    "shipping_profile_id": 55, "return_policy_id": 88, "readiness_state_id": 42, "production_partner_ids": [],
    "payload_version": PAYLOAD_VERSION, "should_auto_renew": True, "is_customizable": True, "is_personalizable": False,
    "processing_min": 1, "processing_max": 3, "price_on_property": [200], "images": [], "materials": [],
    "inventory_products": [
        {"sku": "OLD-S", "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
         "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}]},
        {"sku": "OLD-M", "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
         "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}]},
    ],
}


def _real_for_drafts(rows: Rows) -> None:
    """Makes the generic world usable by a real draft job and a real go-live."""
    cipher = get_cipher()
    spread = random.Random(11)
    far = datetime.now(timezone.utc) + timedelta(days=30)
    for shop in rows.by_table["etsy_connection"]:
        shop["access_token_enc"] = cipher.encrypt(f"{shop['shop_id']}.stub")
        shop["refresh_token_enc"] = cipher.encrypt("stub-refresh")
        shop["token_expires_at"] = far
        shop["currency_code"] = "USD"
    for profile in rows.by_table["listing_profile"]:
        profile["cached_payload"] = REFERENCE
        profile["listing_style"] = "classic"
        # A running system: the profiles' upkeep is spread over its cycle, not all due at once.
        profile["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=spread.uniform(0, 12))
        profile["images_updated_at"] = datetime.now(timezone.utc) - timedelta(hours=spread.uniform(0, 4.5))
    for link in rows.by_table["profile_shop_link"]:
        link.update(shipping_profile_id=155, return_policy_id=188, readiness_state_id=142,
                    checked_at=datetime.now(timezone.utc) - timedelta(hours=spread.uniform(0, 16)))
    for publication in rows.by_table["listing_publication"]:
        publication["scheduled_for"] = None  # the driver schedules some when a step starts
    # The seed's plans are history to read, not work: their photos have no files.
    for slot in rows.by_table["planned_slot"]:
        slot["draft_at"] += timedelta(days=60)
        slot["publish_at"] += timedelta(days=60)


async def main() -> None:
    settings = get_settings()
    if not settings.database_url.endswith("@load-postgres:5432/listyro_load"):
        raise SystemExit("load seed: not the load stack's database: refusing to run")
    accounts = int(os.environ.get("LOAD_ACCOUNTS", "100"))
    shops = int(os.environ.get("LOAD_SHOPS", "3"))
    listings = int(os.environ.get("LOAD_LISTINGS", "500"))
    engine = create_async_engine(settings.database_url)
    started = time.perf_counter()
    for start in range(0, accounts, 10):
        rows = Rows()
        for n in range(start, min(accounts, start + 10)):
            _account(rows, n, shops, listings, TERMS_VERSION)
        _real_for_drafts(rows)
        await rows.flush(engine)
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
        await conn.execute(sa.text("ANALYZE"))
    await engine.dispose()
    print(f"load seed: {accounts} accounts × {shops} shops × {listings} listings in {time.perf_counter() - started:.0f} s; now {NOW:%H:%M} UTC")


if __name__ == "__main__":
    asyncio.run(main())
