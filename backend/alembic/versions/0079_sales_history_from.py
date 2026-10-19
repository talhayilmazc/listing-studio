"""The first day a seller still wants in a shop's profit report ("Delete my sales history").

* ``etsy_connection.sales_history_from``: set to the day the seller deleted the shop's sales
  history; nothing older is stored again (workers/sales.py, workers/ledger.py). NULL (every shop
  until then) means everything the Analytics history setting keeps.

Nothing existing changes.

Revision ID: 0079_sales_history_from
Revises: 0078_mockup_run_designs
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0079_sales_history_from"
down_revision: str | None = "0078_mockup_run_designs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("etsy_connection", sa.Column("sales_history_from", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("etsy_connection", "sales_history_from")
