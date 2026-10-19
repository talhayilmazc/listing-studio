"""A shop may be in several shop groups.

* ``shop_group_member`` (group, shop): one row per shop in a group. Every shop's
  current group (``etsy_connection.group_id``) becomes its row, so every group
  keeps exactly the shops it had.
* ``etsy_connection.group_id`` is dropped (nothing reads it any more).

Downgrade puts each shop back in one group: the first of its groups by the
group's position (a shop that was in several keeps only that one).

Revision ID: 0080_shop_group_members
Revises: 0079_sales_history_from
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0080_shop_group_members"
down_revision: str | None = "0079_sales_history_from"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_group_member",
        sa.Column("group_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("shop_group.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_shop_group_member_connection_id", "shop_group_member", ["connection_id"])
    op.execute(
        "INSERT INTO shop_group_member (group_id, connection_id) "
        "SELECT group_id, id FROM etsy_connection WHERE group_id IS NOT NULL"
    )
    op.drop_index("ix_etsy_connection_group_id", table_name="etsy_connection", if_exists=True)
    op.drop_column("etsy_connection", "group_id")


def downgrade() -> None:
    op.add_column("etsy_connection", sa.Column(
        "group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shop_group.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_etsy_connection_group_id", "etsy_connection", ["group_id"])
    op.execute(
        "UPDATE etsy_connection c SET group_id = ("
        " SELECT m.group_id FROM shop_group_member m JOIN shop_group g ON g.id = m.group_id"
        " WHERE m.connection_id = c.id ORDER BY g.position, g.name LIMIT 1)"
    )
    op.drop_index("ix_shop_group_member_connection_id", table_name="shop_group_member")
    op.drop_table("shop_group_member")
