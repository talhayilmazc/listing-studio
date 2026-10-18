"""Half-made drafts the seller took off the list.

* ``draft_dismissal``: the shop and Etsy listing id of a half-made draft the seller
  took off the "Half-made drafts" list ("I deleted it on Etsy"). The list is now also
  built from the create snapshots of failed jobs (a draft whose batch was deleted), so
  removing the draft attempt alone no longer hides it. Removed with the shop and after
  90 days. Nothing existing changes.

Revision ID: 0075_draft_dismissal
Revises: 0074_mockup_fill
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0075_draft_dismissal"
down_revision: str | None = "0074_mockup_fill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "draft_dismissal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("etsy_listing_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "etsy_listing_id", name="uq_draft_dismissal_shop_listing"),
    )
    op.create_index("ix_draft_dismissal_tenant_id", "draft_dismissal", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_draft_dismissal_tenant_id", table_name="draft_dismissal")
    op.drop_table("draft_dismissal")
