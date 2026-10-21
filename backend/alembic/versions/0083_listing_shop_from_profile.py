"""A listing's shop comes from its profile, not from its batch or group (B3).

From now on a listing goes, unless the seller chose shops for it, to its profile's main
shop (``pipeline/shop_targets.own_shop``); the batch's shop and each group's shop no longer
decide anything (the columns stay, unused for decisions). So that no written listing changes
where it goes, every listing that followed the default (``target_shops`` NULL) and whose old
default (its group's shop, else its batch's) differs from its profile's main shop now names
that old shop explicitly. Nothing else changes; nothing is sent.

Downgrade: the listings this set (one shop, the old default, not their profile's main
shop) follow the default again.

Revision ID: 0083_listing_shop_from_profile
Revises: 0082_shop_setting_map
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0083_listing_shop_from_profile"
down_revision: str | None = "0082_shop_setting_map"
branch_labels = None
depends_on = None

content = sa.table(
    "generated_content",
    sa.column("id"), sa.column("batch_id"), sa.column("asset_id"), sa.column("listing_profile_id"),
    sa.column("target_shops", sa.JSON()),
)
asset = sa.table("asset", sa.column("id"), sa.column("group_key"))
batch = sa.table("upload_batch", sa.column("id"), sa.column("connection_id"))
group = sa.table("listing_group_setting", sa.column("batch_id"), sa.column("group_key"), sa.column("connection_id"))
profile = sa.table("listing_profile", sa.column("id"), sa.column("connection_id"))


def _old_defaults(bind) -> list[tuple[object, str, str | None]]:  # noqa: ANN001
    """(listing id, its old default shop, its profile's main shop) for every listing whose
    old default differs from its profile's main shop."""
    groups = {(str(b), g or ""): c for b, g, c in bind.execute(sa.select(group.c.batch_id, group.c.group_key, group.c.connection_id))}
    batches = {str(b): c for b, c in bind.execute(sa.select(batch.c.id, batch.c.connection_id))}
    keys = {str(a): k for a, k in bind.execute(sa.select(asset.c.id, asset.c.group_key))}
    mains = {str(p): c for p, c in bind.execute(sa.select(profile.c.id, profile.c.connection_id))}
    out = []
    rows = bind.execute(sa.select(content.c.id, content.c.batch_id, content.c.asset_id, content.c.listing_profile_id, content.c.target_shops))
    for cid, batch_id, asset_id, profile_id, targets in rows:
        old = groups.get((str(batch_id), keys.get(str(asset_id)) or "")) or batches.get(str(batch_id))
        main = mains.get(str(profile_id)) if profile_id is not None else None
        if old is not None and main is not None and str(old) != str(main):
            out.append((cid, str(old), str(main), targets))
    return out


def upgrade() -> None:
    bind = op.get_bind()
    for cid, old, _main, targets in _old_defaults(bind):
        if targets is None:
            bind.execute(content.update().where(content.c.id == cid).values(target_shops=[old]))


def downgrade() -> None:
    bind = op.get_bind()
    for cid, old, _main, targets in _old_defaults(bind):
        if targets == [old]:
            bind.execute(content.update().where(content.c.id == cid).values(target_shops=None))
