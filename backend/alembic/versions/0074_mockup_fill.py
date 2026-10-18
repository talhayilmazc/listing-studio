"""Designs fill the print area; each design file keeps its trim box.

* ``mockup_design.trims`` / ``mockup_design_version.trims``: each file's trim box (its
  transparent padding left out, a small safety edge kept), measured at upload; the
  files themselves are never changed. NULL: measured when the file is next printed.
* ``mockup_photo.size``: the default becomes 1.0 (the design touches the area on its
  limiting side). Photos still at the old default 0.9 move to 1.0: that value was the
  default every photo got, and it is what left the empty band under every design.
  Any other size the seller chose stays.

Revision ID: 0074_mockup_fill
Revises: 0073_mockup_subgroups
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0074_mockup_fill"
down_revision: str | None = "0073_mockup_subgroups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_design", sa.Column("trims", sa.JSON(), nullable=True))
    op.add_column("mockup_design_version", sa.Column("trims", sa.JSON(), nullable=True))
    with op.batch_alter_table("mockup_photo") as table:
        table.alter_column("size", server_default="1.0", existing_type=sa.Float(), existing_nullable=False)
    photo = sa.table("mockup_photo", sa.column("size", sa.Float()))
    op.get_bind().execute(photo.update().where(photo.c.size.between(0.8999, 0.9001)).values(size=1.0))


def downgrade() -> None:
    with op.batch_alter_table("mockup_photo") as table:
        table.alter_column("size", server_default="0.9", existing_type=sa.Float(), existing_nullable=False)
    op.drop_column("mockup_design_version", "trims")
    op.drop_column("mockup_design", "trims")
