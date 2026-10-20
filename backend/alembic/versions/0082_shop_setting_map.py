"""One map per account of shop settings across shops (v8 §C).

``shop_setting_map``: (source shop, source shipping profile / return policy / processing
profile id) -> (target shop, that shop's own id). Every profile whose reference uses the
source setting reads it, so a setting is set up once per shop however many reference
listings use it. Before, each profile's link kept its own result: creating "MemoPaid" in
a shop for one reference listing left a second reference listing using the same shipping
profile at "no match in this shop".

The map is built from what the links already hold: for each link outside its profile's
main shop, each id it holds stands for the setting the profile's reference uses
(``shipping_profile_id``, ``return_policy_id``, ``readiness_state_id``; the processing
profile falls back to the first one the reference's offerings carry, as
``pipeline/links.py::wanted`` does). Where two links chose differently for the same
source setting and shop, the most recently checked one wins (the counts are printed).
Links are not changed here: the worker's one-time job fills every link from the map
(``workers/links.py::resolve_shop_settings_once``). The downgrade drops the table; the
links keep their ids.

Revision ID: 0082_shop_setting_map
Revises: 0081_design_variants
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0082_shop_setting_map"
down_revision: str | None = "0081_design_variants"
branch_labels = None
depends_on = None

# Frozen here, as they were when this migration was written (pipeline/links.py).
KEYS = {"shipping_profile": "shipping_profile_id", "return_policy": "return_policy_id",
        "readiness_state": "readiness_state_id"}


def _wanted(payload: dict[str, Any] | None, resource: str) -> int | None:
    value = (payload or {}).get(KEYS[resource])
    if resource == "readiness_state" and value in (None, ""):
        for product in (payload or {}).get("inventory_products") or []:
            for offering in product.get("offerings") or []:
                if offering.get("readiness_state_id") not in (None, ""):
                    return int(offering["readiness_state_id"])
        return None
    return int(value) if value not in (None, "") else None


def upgrade() -> None:
    op.create_table(
        "shop_setting_map",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("source_connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("target_connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("how", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "resource", "source_connection_id", "source_id", "target_connection_id",
                            name="uq_shop_setting_map"),
    )
    op.create_index("ix_shop_setting_map_tenant_id", "shop_setting_map", ["tenant_id"])
    op.create_index("ix_shop_setting_map_source_connection_id", "shop_setting_map", ["source_connection_id"])
    op.create_index("ix_shop_setting_map_target_connection_id", "shop_setting_map", ["target_connection_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT l.tenant_id, p.connection_id AS source, l.connection_id AS target, l.shipping_profile_id, "
        "l.return_policy_id, l.readiness_state_id, p.cached_payload, l.checked_at, l.created_at "
        "FROM profile_shop_link l JOIN listing_profile p ON p.id = l.profile_id "
        "WHERE l.connection_id <> p.connection_id"
    )).mappings().all()
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    best: dict[tuple, tuple[datetime, int]] = {}
    conflicts = 0
    for row in rows:
        payload = row["cached_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        when = row["checked_at"] or row["created_at"] or epoch
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        for resource, column in KEYS.items():
            target_id, source_id = row[column], _wanted(payload, resource)
            if target_id is None or source_id is None:
                continue
            key = (row["tenant_id"], resource, row["source"], source_id, row["target"])
            if key in best and best[key][1] != int(target_id):
                conflicts += 1
            if key not in best or when >= best[key][0]:
                best[key] = (when, int(target_id))
    table = sa.table(
        "shop_setting_map",
        sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()), sa.column("resource", sa.Text()),
        sa.column("source_connection_id", sa.Uuid()), sa.column("source_id", sa.BigInteger()),
        sa.column("target_connection_id", sa.Uuid()), sa.column("target_id", sa.BigInteger()),
        sa.column("how", sa.Text()),
    )
    values = [
        {"id": uuid.uuid4(), "tenant_id": tenant, "resource": resource, "source_connection_id": source,
         "source_id": source_id, "target_connection_id": target, "target_id": target_id, "how": "earlier choice"}
        for (tenant, resource, source, source_id, target), (_when, target_id) in best.items()
    ]
    if values:
        op.bulk_insert(table, values)
    print(f"0082: {len(values)} shop settings mapped from {len(rows)} profile links; "
          f"{conflicts} chose differently for the same setting and shop (the latest kept)")


def downgrade() -> None:
    op.drop_index("ix_shop_setting_map_target_connection_id", table_name="shop_setting_map")
    op.drop_index("ix_shop_setting_map_source_connection_id", table_name="shop_setting_map")
    op.drop_index("ix_shop_setting_map_tenant_id", table_name="shop_setting_map")
    op.drop_table("shop_setting_map")

