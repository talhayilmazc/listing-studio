"""A listing whose own title or tags are empty takes the text typed for one of its shops.

Production, 2026-10-28: with a shop picked in the menu, the review card saved what the seller
typed on a hand-written listing as that shop's own text (``shop_overrides``), leaving the
listing's own title and tags empty; every other screen and check (the batch page, every
other shop, Approve all) read the listing's text and showed it empty. From now on text typed
for a shop also fills the listing's empty fields (pipeline/shop_listing.py::fill_listing_text);
this fills the rows already stored the same way. Only an empty title or empty tags are
filled, never overwritten; each shop keeps its own copy. The shop chosen is the one the
listing is on Etsy in first (its earliest draft), else the first shop in the seller's order.

Every filled field is written to one ``audit_log`` row (``migration.listing_text_filled``:
listing ids and field names, no text), and downgrade empties those fields again where they
still hold what was filled.

Revision ID: 0094_listing_text_from_shop
Revises: 0093_listing_shop_overrides
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0094_listing_text_from_shop"
down_revision: str | None = "0093_listing_shop_overrides"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ACTION = "migration.listing_text_filled"

content = sa.table(
    "generated_content",
    sa.column("id", sa.Uuid()), sa.column("title", sa.Text()), sa.column("tags", postgresql.ARRAY(sa.Text())),
    sa.column("shop_overrides", JSON),
)
publication = sa.table(
    "listing_publication",
    sa.column("content_id", sa.Uuid()), sa.column("connection_id", sa.Uuid()), sa.column("state", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
shop = sa.table("etsy_connection", sa.column("id", sa.Uuid()), sa.column("position", sa.Integer()))
audit = sa.table(
    "audit_log",
    sa.column("id", sa.Uuid()), sa.column("action", sa.Text()), sa.column("details", JSON),
)


def _empty(name: str, value: object) -> bool:
    return not value


def _shop_value(entry: object, name: str) -> object:
    if not isinstance(entry, dict):
        return None
    value = entry.get(name)
    return value or None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(content.c.id, content.c.title, content.c.tags, content.c.shop_overrides)
                        .where(content.c.shop_overrides.is_not(None))).all()
    positions = {r.id: r.position or 0 for r in bind.execute(sa.select(shop.c.id, shop.c.position))}
    filled: list[dict] = []
    for row in rows:
        overrides = row.shop_overrides or {}
        if not isinstance(overrides, dict) or not overrides:
            continue
        wanted = [n for n, v in (("title", row.title), ("tags", row.tags)) if _empty(n, v)]
        if not wanted:
            continue
        drafted = [str(p.connection_id) for p in bind.execute(
            sa.select(publication.c.connection_id).where(
                publication.c.content_id == row.id, publication.c.state != "deleted_on_etsy",
            ).order_by(publication.c.created_at))]
        order = list(dict.fromkeys(drafted + sorted(
            overrides, key=lambda k: positions.get(_uuid(k), 10**6))))
        values: dict[str, object] = {}
        for name in wanted:
            for key in order:
                value = _shop_value(overrides.get(key), name)
                if value:
                    values[name] = [str(t) for t in value] if name == "tags" else str(value)
                    break
        if values:
            bind.execute(sa.update(content).where(content.c.id == row.id).values(**values))
            filled.append({"content_id": str(row.id), "fields": sorted(values)})
    if filled:
        bind.execute(sa.insert(audit).values(id=uuid.uuid4(), action=ACTION, details={"filled": filled}))


def _uuid(key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(key))
    except ValueError:
        return None


def downgrade() -> None:
    bind = op.get_bind()
    for (details,) in bind.execute(sa.select(audit.c.details).where(audit.c.action == ACTION)).all():
        for item in (details or {}).get("filled", []):
            row = bind.execute(sa.select(content.c.title, content.c.tags, content.c.shop_overrides)
                               .where(content.c.id == uuid.UUID(item["content_id"]))).first()
            if row is None:
                continue
            shop_values = [e for e in (row.shop_overrides or {}).values() if isinstance(e, dict)]
            back: dict[str, object] = {}
            for name in item["fields"]:
                current = row.title if name == "title" else row.tags
                if any(_shop_value(e, name) == current for e in shop_values):
                    back[name] = None if name == "title" else []
            if back:
                bind.execute(sa.update(content).where(content.c.id == uuid.UUID(item["content_id"])).values(**back))
    bind.execute(sa.delete(audit).where(audit.c.action == ACTION))
