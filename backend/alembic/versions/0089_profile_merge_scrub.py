"""profile_merge keeps no Etsy content and goes with its profile (quality pass 2026-10).

Migration 0046 recorded every profile merge in ``profile_merge`` so its downgrade could undo it:
the merged profile's whole row as JSON, ``cached_payload`` included. That is Etsy listing content
(description, prices, images, attributes), which may be kept 24 hours at most, and the rows had no
link to an account, so they outlived shop disconnects and account deletions.

Now:

* ``cached_payload`` and ``refresh_error`` are removed from every stored row (a downgrade past
  0046 still restores the profile, which reads its reference again on its next refresh);
* rows whose kept profile is gone are deleted;
* ``kept_profile_id`` becomes a foreign key to ``listing_profile`` with ON DELETE CASCADE (and an
  index), so a row goes with its profile, its shop and its account.

The downgrade drops the foreign key and the index; the removed content cannot come back.

Revision ID: 0089_profile_merge_scrub
Revises: 0088_fk_indexes
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision: str = "0089_profile_merge_scrub"
down_revision: str | None = "0088_fk_indexes"
branch_labels = None
depends_on = None

#: Keys of the stored profile row that hold Etsy content, or text read from Etsy.
ETSY_KEYS = ("cached_payload", "refresh_error")

merge = sa.table("profile_merge", sa.column("id", sa.Uuid()), sa.column("kept_profile_id", sa.Uuid()),
                 sa.column("merged", sa.Text()))
profile = sa.table("listing_profile", sa.column("id", sa.Uuid()))


def scrubbed(merged: str) -> str:
    """The stored row without its Etsy content."""
    try:
        row = json.loads(merged)
    except (TypeError, ValueError):
        return json.dumps({})
    if isinstance(row, dict):
        for key in ETSY_KEYS:
            row.pop(key, None)
    return json.dumps(row, default=str)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(merge.delete().where(~merge.c.kept_profile_id.in_(sa.select(profile.c.id))))
    for row_id, merged in bind.execute(sa.select(merge.c.id, merge.c.merged)).all():
        bind.execute(merge.update().where(merge.c.id == row_id).values(merged=scrubbed(merged)))
    op.create_index("ix_profile_merge_kept_profile_id", "profile_merge", ["kept_profile_id"])
    op.create_foreign_key("fk_profile_merge_kept_profile", "profile_merge", "listing_profile",
                          ["kept_profile_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("fk_profile_merge_kept_profile", "profile_merge", type_="foreignkey")
    op.drop_index("ix_profile_merge_kept_profile_id", table_name="profile_merge")
