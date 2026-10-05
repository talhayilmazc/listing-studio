"""Publication records are marked, not deleted, when the draft is gone on Etsy

A draft the seller deleted in Shop Manager used to take its
``listing_publication`` row with it (the "Publish now" 404 path), so the record
that the app created it vanished and counts dropped with nothing in the audit
log. The row now stays: ``state`` becomes "deleted_on_etsy" and this column
says when the app found out.

Revision ID: 0045_publication_deleted_on_etsy
Revises: 0044_sales_reread
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_publication_deleted_on_etsy"
down_revision: str | None = "0044_sales_reread"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_publication", sa.Column("deleted_on_etsy_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # The marked rows stay (as state "deleted_on_etsy"); only the timestamp goes.
    op.drop_column("listing_publication", "deleted_on_etsy_at")
