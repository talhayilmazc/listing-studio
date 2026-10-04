"""Analytics import: statement months, per-order rows, shop-level daily ads, sale lines

Adds what "Import from Etsy" stores (totals per category for a statement
month, its per-order and per-listing rows, the Ads report's daily shop totals)
and the per-order sale lines read from Etsy's transactions.

Deletes every row of ``ad_spend``: they were written by the old Ads import,
which tied report rows to listings by id or by **title**. Etsy's real Ads
export has no listing column and titles must not be used to guess one, so that
import is gone and ad spend is shop level from now on. The count is printed.

Revision ID: 0043_statement_import
Revises: 0042_etsy_ceiling_default
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_statement_import"
down_revision: str | None = "0042_etsy_ceiling_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = postgresql.JSONB()


def _shop() -> sa.Column:
    return sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True)


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "sale_line",
        _shop(),
        sa.Column("transaction_id", sa.BigInteger(), primary_key=True),
        _tenant(),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("shipping_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.Text()),
    )
    op.create_index("ix_sale_line_receipt", "sale_line", ["connection_id", "receipt_id"])
    op.create_index("ix_sale_line_day", "sale_line", ["connection_id", "day"])
    op.add_column("sales_sync", sa.Column("has_lines", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "statement_import",
        _shop(),
        sa.Column("month", sa.Date(), primary_key=True),
        _tenant(),
        sa.Column("currency", sa.Text()),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("net_minor", sa.BigInteger(), nullable=False),
        sa.Column("totals", JSON, nullable=False),
        sa.Column("counts", JSON, nullable=False),
        sa.Column("credits", JSON, nullable=False),
        sa.Column("deposits", JSON, nullable=False),
        sa.Column("unrecognised", JSON, nullable=False),
        sa.Column("notes", JSON, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "statement_order",
        _shop(),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("receipt_id", sa.BigInteger(), primary_key=True),
        _tenant(),
        sa.Column("day", sa.Date()),
        sa.Column("amounts", JSON, nullable=False),
    )
    op.create_table(
        "statement_listing_fee",
        _shop(),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), primary_key=True),
        _tenant(),
        sa.Column("fees", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credits_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "ad_charge",
        _shop(),
        sa.Column("click_day", sa.Date(), primary_key=True),
        _tenant(),
        sa.Column("posted", sa.Date(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_ad_charge_month", "ad_charge", ["month"])
    op.create_table(
        "ads_daily",
        _shop(),
        sa.Column("day", sa.Date(), primary_key=True),
        _tenant(),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spend_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.Text()),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    bind = op.get_bind()
    rows, uploads, shops = bind.execute(
        sa.text("SELECT count(*), count(DISTINCT upload_id), count(DISTINCT connection_id) FROM ad_spend")
    ).one()
    bind.execute(sa.text("DELETE FROM ad_spend"))
    print(f"  ad_spend: removed {rows} per-listing ad row(s) from {uploads} upload(s) in {shops} shop(s) (the old title-matching import)")


def downgrade() -> None:
    # The deleted per-listing ad rows are not restored: they are not wanted back.
    for table in ("ads_daily", "ad_charge", "statement_listing_fee", "statement_order", "statement_import"):
        op.drop_table(table)
    op.drop_column("sales_sync", "has_lines")
    op.drop_table("sale_line")
