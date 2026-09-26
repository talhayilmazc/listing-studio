"""sales_sync: the seller's sales read, resumable and paced (v7 §C1)

Revision ID: 0028_sales_sync
Revises: 0027_tenant_time_zone
Create Date: 2026-09-26

One row per shop: where the first 13-month read stands (offset, counts, the
oldest and newest sale counted as created time and transaction id), the
estimate shown before it starts, and the requests it has used today and in
total. Counts and cursors only; deleted with the shop.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_sales_sync"
down_revision: str | None = "0027_tenant_time_zone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_sync",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="estimating"),
        sa.Column("direction", sa.Text()),
        sa.Column("total_count", sa.Integer()),
        sa.Column("window_count", sa.Integer()),
        sa.Column("pages_estimate", sa.Integer()),
        sa.Column("start_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.Date()),
        sa.Column("oldest_ts", sa.BigInteger()),
        sa.Column("oldest_id", sa.BigInteger()),
        sa.Column("newest_ts", sa.BigInteger()),
        sa.Column("newest_id", sa.BigInteger()),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_day", sa.Date()),
        sa.Column("requests_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_update_requests", sa.Integer()),
        sa.Column("resumes_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("lock_until", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sales_sync")
