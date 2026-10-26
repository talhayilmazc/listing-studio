"""Our records follow what the seller did on Etsy (2026-10-26).

``listing_publication`` gains three columns:

* ``published_by``: "seller_on_etsy" when the seller published the draft in Shop Manager
  and the app found it live (it sent nothing); NULL when the app published it, or not live.
* ``etsy_checked_at``: when the app last read the listing's state on Etsy (the upkeep
  check reads a draft at most once every 6 hours).
* ``former_content_id``: the listing a draft deleted on Etsy was made from. Marking it
  deleted detaches ``content_id`` (so it can be drafted again); this keeps the card able
  to say "Deleted on Etsy". SET NULL with the content, indexed.

No row changes: drafts deleted before keep no link (it was never stored). The downgrade
drops the three columns.

Revision ID: 0092_etsy_reconcile
Revises: 0091_approval_record
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0092_etsy_reconcile"
down_revision: str | None = "0091_approval_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_publication", sa.Column("published_by", sa.Text(), nullable=True))
    op.add_column("listing_publication", sa.Column("etsy_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listing_publication", sa.Column("former_content_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "listing_publication_former_content_id_fkey", "listing_publication", "generated_content",
        ["former_content_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_listing_publication_former_content_id", "listing_publication", ["former_content_id"])


def downgrade() -> None:
    op.drop_index("ix_listing_publication_former_content_id", table_name="listing_publication")
    op.drop_constraint("listing_publication_former_content_id_fkey", "listing_publication", type_="foreignkey")
    op.drop_column("listing_publication", "former_content_id")
    op.drop_column("listing_publication", "etsy_checked_at")
    op.drop_column("listing_publication", "published_by")
