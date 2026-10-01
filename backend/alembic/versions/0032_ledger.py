"""ledger_daily and ledger_sync: the shop's real Etsy fees and ad spend

Revision ID: 0032_ledger
Revises: 0031_listing_counts
Create Date: 2026-09-27

From the payment account ledger (getShopPaymentAccountLedgerEntries,
transactions_r): per day and entry type, the summed amount, entry count and
currency. The entries themselves are not kept. Kept 13 months; deleted with
the shop.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_ledger"
down_revision: str | None = "0031_listing_counts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_daily",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("ledger_type", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("entries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.Text()),
    )
    op.create_index("ix_ledger_daily_tenant_day", "ledger_daily", ["tenant_id", "day"])
    op.create_table(
        "ledger_sync",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="estimating"),
        sa.Column("window_start", sa.BigInteger()),
        sa.Column("window_end", sa.BigInteger()),
        sa.Column("total_count", sa.Integer()),
        sa.Column("next_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_until", sa.BigInteger()),
        sa.Column("update_end", sa.BigInteger()),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_day", sa.Date()),
        sa.Column("requests_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resumes_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("lock_until", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ledger_sync")
    op.drop_index("ix_ledger_daily_tenant_day", table_name="ledger_daily")
    op.drop_table("ledger_daily")
