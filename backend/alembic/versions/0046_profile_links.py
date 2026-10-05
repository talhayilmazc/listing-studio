"""One profile across shops (v8 §C): shop links, shop groups, and merging
same-named profiles whose shared settings are identical

* ``profile_shop_link``: a profile used in one shop, with that shop's own ids.
  Every existing profile gets a link to its own (main) shop.
* ``shop_group`` and ``etsy_connection.group_id``: named groups of shops.
* ``listing_profile.reference_listing_id`` may be NULL (a profile whose main
  shop was disconnected waits for a reference in its new main shop).
* Same-named profiles of one account in different shops become one profile
  **only when everything they share is identical** (template, title prefix,
  personalization, listing style and title bounds, category, prices and
  variations, who/when made, auto-renew, description, attributes, number of
  size charts, product costs) and both references are still held (the payload
  lapses after 24 hours; then they cannot be compared and stay separate, and
  the Profiles page offers "Link these"). The oldest is kept; the others become
  its links, holding the ids their own references had. Everything that named
  them names the kept one. Each merge is printed and recorded in
  ``profile_merge``, so the downgrade puts every profile back.

Revision ID: 0046_profile_links
Revises: 0045_publication_deleted_on_etsy
Create Date: 2026-10-06
"""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0046_profile_links"
down_revision: str | None = "0045_publication_deleted_on_etsy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCES = (
    ("upload_batch", "size_chart_profile_id"),
    ("listing_group_setting", "profile_id"),
    ("listing_group_setting", "size_chart_profile_id"),
    ("generated_content", "listing_profile_id"),
    ("listing_publication", "profile_id"),
)
PROFILE_COLUMNS = (
    "id", "tenant_id", "connection_id", "name", "reference_listing_id", "cached_payload", "fixed_image_ids",
    "content_template", "personalization", "title_prefix", "listing_style", "title_min_length",
    "title_max_length", "source", "confirmed", "updated_at", "images_updated_at", "refresh_error",
    "refresh_failed_at", "created_at",
)
SHARED_KEYS = (
    "taxonomy_id", "price", "currency", "who_made", "when_made", "is_supply", "is_customizable",
    "should_auto_renew", "description", "price_on_property", "quantity_on_property", "sku_on_property",
    "personalization",
)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _money(value: Any) -> float:
    if isinstance(value, dict) and value.get("divisor"):
        return round(float(value.get("amount") or 0) / float(value["divisor"]), 2)
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _signature(row: dict[str, Any], costs: Any) -> Any:
    """Frozen copy of pipeline/links.shared_signature, as of this revision."""
    payload = _json(row["cached_payload"])
    if not payload:
        return None
    variations = sorted((
        (sorted((str(v.get("property_name") or v.get("property_id")), tuple(str(x) for x in v.get("values") or []))
                for v in p.get("property_values") or []),
         sorted((_money(o.get("price")), bool(o.get("is_enabled", True))) for o in p.get("offerings") or []))
        for p in payload.get("inventory_products") or []), key=repr)
    attributes = sorted(
        (str(r.get("property_id")), tuple(sorted(str(v) for v in r.get("value_ids") or [])),
         tuple(sorted(str(v) for v in r.get("values") or [])))
        for r in payload.get("attributes") or [])
    return (
        row["content_template"], (row["title_prefix"] or "").strip(), repr(_json(row["personalization"])),
        row["listing_style"], row["title_min_length"], row["title_max_length"],
        tuple(repr(payload.get(k)) for k in SHARED_KEYS), repr(variations), repr(attributes),
        len(_json(row["fixed_image_ids"]) or []), repr(costs or None),
    )


def _uuid_col(name: str, *args: Any, **kw: Any) -> sa.Column:
    return sa.Column(name, sa.Uuid(), *args, **kw)


def upgrade() -> None:
    op.create_table(
        "shop_group",
        _uuid_col("id", primary_key=True),
        _uuid_col("tenant_id", sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_shop_group_name"),
    )
    op.add_column("etsy_connection", _uuid_col("group_id", sa.ForeignKey("shop_group.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_etsy_connection_group_id", "etsy_connection", ["group_id"])
    op.create_table(
        "profile_shop_link",
        _uuid_col("id", primary_key=True),
        _uuid_col("tenant_id", sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid_col("profile_id", sa.ForeignKey("listing_profile.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid_col("connection_id", sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("shipping_profile_id", sa.BigInteger()),
        sa.Column("return_policy_id", sa.BigInteger()),
        sa.Column("readiness_state_id", sa.BigInteger()),
        sa.Column("production_partner_ids", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("status", sa.Text(), nullable=False, server_default="checking"),
        sa.Column("notes", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("pending_create", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("profile_id", "connection_id", name="uq_profile_shop_link"),
    )
    op.alter_column("listing_profile", "reference_listing_id", existing_type=sa.BigInteger(), nullable=True)
    # What the merges below changed, so the downgrade can undo each one.
    op.create_table(
        "profile_merge",
        _uuid_col("id", primary_key=True),
        _uuid_col("kept_profile_id", nullable=False),
        sa.Column("merged", sa.Text(), nullable=False),  # the merged profile's row, as JSON
        sa.Column("repointed", sa.Text(), nullable=False),  # {"table.column": [row ids]}
        _uuid_col("link_id", nullable=False),  # the link it became
        sa.Column("costs", sa.Text()),  # its product costs, as JSON
    )

    bind = op.get_bind()
    columns = ", ".join(PROFILE_COLUMNS)
    rows = [dict(r._mapping) for r in bind.execute(sa.text(f"SELECT {columns} FROM listing_profile ORDER BY created_at, id"))]
    for row in rows:
        bind.execute(
            sa.text("INSERT INTO profile_shop_link (id, tenant_id, profile_id, connection_id, status) "
                    "VALUES (:id, :tenant, :profile, :shop, 'ready')"),
            {"id": uuid.uuid4(), "tenant": row["tenant_id"], "profile": row["id"], "shop": row["connection_id"]},
        )
    print(f"  profiles: {len(rows)} profile(s), each linked to its own shop")

    costs_of: dict[Any, dict[str, Any]] = {}
    for tenant_id, settings in bind.execute(sa.text("SELECT id, cost_settings FROM tenant")).all():
        costs_of[tenant_id] = (_json(settings) or {}).get("profile_costs") or {}

    groups: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["tenant_id"], row["name"].strip().casefold()), []).append(row)
    merged_count = 0
    for (tenant_id, _name), members in groups.items():
        if len({m["connection_id"] for m in members}) < 2:
            continue
        keep = members[0]
        keep_sig = _signature(keep, costs_of.get(tenant_id, {}).get(str(keep["id"])))
        shops_taken = {keep["connection_id"]}
        for other in members[1:]:
            if other["connection_id"] in shops_taken:
                print(f"  profiles: kept '{other['name']}' ({other['id']}) separate: a second profile of the same shop")
                continue
            sig = _signature(other, costs_of.get(tenant_id, {}).get(str(other["id"])))
            if keep_sig is None or sig is None or sig != keep_sig:
                why = "its reference has lapsed, so it cannot be compared" if keep_sig is None or sig is None else "its shared settings differ"
                print(f"  profiles: kept '{other['name']}' ({other['id']}) separate: {why}; offered as \"Link these\"")
                continue
            payload = _json(other["cached_payload"]) or {}
            # Its main-shop link becomes the kept profile's link to that shop.
            link_id = bind.execute(sa.text(
                "SELECT id FROM profile_shop_link WHERE profile_id = :p AND connection_id = :c"),
                {"p": other["id"], "c": other["connection_id"]}).scalar_one()
            bind.execute(sa.text(
                "UPDATE profile_shop_link SET profile_id = :keep, shipping_profile_id = :ship, return_policy_id = :ret, "
                "readiness_state_id = :ready, production_partner_ids = :partners WHERE id = :id"),
                {"keep": keep["id"], "ship": payload.get("shipping_profile_id"), "ret": payload.get("return_policy_id"),
                 "ready": payload.get("readiness_state_id"),
                 "partners": json.dumps(list(payload.get("production_partner_ids") or [])), "id": link_id})
            repointed: dict[str, list[str]] = {}
            for table, column in REFERENCES:
                ids = [str(i) for i in bind.execute(sa.text(f"SELECT id FROM {table} WHERE {column} = :p"), {"p": other["id"]}).scalars()]
                if ids:
                    bind.execute(sa.text(f"UPDATE {table} SET {column} = :keep WHERE {column} = :p"),
                                 {"keep": keep["id"], "p": other["id"]})
                    repointed[f"{table}.{column}"] = ids
            snapshot = {k: (str(v) if isinstance(v, (uuid.UUID,)) else v) for k, v in other.items()}
            for k in ("cached_payload", "fixed_image_ids", "personalization"):
                snapshot[k] = _json(snapshot[k])
            for k in ("updated_at", "images_updated_at", "refresh_failed_at", "created_at"):
                snapshot[k] = snapshot[k].isoformat() if snapshot[k] is not None else None
            other_costs = costs_of.get(tenant_id, {}).pop(str(other["id"]), None)
            bind.execute(sa.text(
                "INSERT INTO profile_merge (id, kept_profile_id, merged, repointed, link_id, costs) "
                "VALUES (:id, :keep, :merged, :repointed, :link, :costs)"),
                {"id": uuid.uuid4(), "keep": keep["id"], "merged": json.dumps(snapshot),
                 "repointed": json.dumps(repointed), "link": link_id,
                 "costs": json.dumps(other_costs) if other_costs is not None else None})
            bind.execute(sa.text("DELETE FROM listing_profile WHERE id = :p"), {"p": other["id"]})
            shops_taken.add(other["connection_id"])
            merged_count += 1
            print(f"  profiles: merged '{other['name']}' ({other['id']}) into {keep['id']}; "
                  f"{sum(len(v) for v in repointed.values())} row(s) now name the kept profile")
    for tenant_id, costs in costs_of.items():
        if costs:
            settings = _json(bind.execute(sa.text("SELECT cost_settings FROM tenant WHERE id = :t"), {"t": tenant_id}).scalar()) or {}
            settings["profile_costs"] = costs
            bind.execute(sa.text("UPDATE tenant SET cost_settings = :s WHERE id = :t"), {"s": json.dumps(settings), "t": tenant_id})
    print(f"  profiles: {merged_count} merged")


def downgrade() -> None:
    bind = op.get_bind()
    merges = bind.execute(sa.text("SELECT kept_profile_id, merged, repointed, link_id, costs FROM profile_merge")).all()
    for kept_id, merged, repointed, _link_id, costs in merges:
        row = json.loads(merged)
        for k in ("cached_payload", "fixed_image_ids", "personalization"):
            row[k] = json.dumps(row[k]) if row[k] is not None else None
        names = ", ".join(PROFILE_COLUMNS)
        values = ", ".join(f":{c}" for c in PROFILE_COLUMNS)
        bind.execute(sa.text(f"INSERT INTO listing_profile ({names}) VALUES ({values})"), row)
        for target, ids in json.loads(repointed).items():
            table, column = target.split(".")
            for row_id in ids:
                bind.execute(sa.text(f"UPDATE {table} SET {column} = :p WHERE id = :id"), {"p": row["id"], "id": row_id})
        if costs:
            tenant_id = row["tenant_id"]
            settings = _json(bind.execute(sa.text("SELECT cost_settings FROM tenant WHERE id = :t"), {"t": tenant_id}).scalar()) or {}
            settings.setdefault("profile_costs", {})[row["id"]] = json.loads(costs)
            bind.execute(sa.text("UPDATE tenant SET cost_settings = :s WHERE id = :t"), {"s": json.dumps(settings), "t": tenant_id})
        print(f"  profiles: '{row['name']}' ({row['id']}) separated again from {kept_id}")
    left = bind.execute(sa.text("SELECT count(*) FROM listing_profile WHERE reference_listing_id IS NULL")).scalar()
    if left:
        raise RuntimeError(
            f"{left} profile(s) have no reference listing (their main shop was disconnected after this revision); "
            "choose a reference for them or delete them before downgrading"
        )
    op.drop_table("profile_merge")
    op.alter_column("listing_profile", "reference_listing_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_table("profile_shop_link")
    op.drop_index("ix_etsy_connection_group_id", table_name="etsy_connection")
    op.drop_column("etsy_connection", "group_id")
    op.drop_table("shop_group")
