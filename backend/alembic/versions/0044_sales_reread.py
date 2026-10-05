"""Sales re-read: mark the shops in it, so they share a nightly slice of the budget

Shops whose sales were read before order lines were kept are read once more.
``sales_sync.reread`` says where that stands (NULL / "reading" / "done"), so all
of them together can be held to a share of the app's daily Etsy budget and read
one at a time (workers/sales.py).

A re-read the previous version already began is marked here, so it comes under
the same cap: a read in progress for a shop that had completed one before. The
count is printed.

Revision ID: 0044_sales_reread
Revises: 0043_statement_import
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_sales_reread"
down_revision: str | None = "0043_statement_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales_sync", sa.Column("reread", sa.Text(), nullable=True))
    marked = op.get_bind().execute(
        sa.text(
            """
            UPDATE sales_sync SET reread = 'reading'
            WHERE state IN ('reading', 'waiting') AND has_lines
              AND connection_id IN (SELECT id FROM etsy_connection WHERE sales_synced_at IS NOT NULL)
            """
        )
    )
    print(f"0044: {marked.rowcount} sales re-read(s) already under way are now under the nightly cap")


def downgrade() -> None:
    op.drop_column("sales_sync", "reread")
