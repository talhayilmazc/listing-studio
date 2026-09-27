"""The product allowance: per-seller amount and period, usage events, app settings

Revision ID: 0030_allowance
Revises: 0029_seller_trademark_filter
Create Date: 2026-09-27

tenant.allowance_amount/allowance_period (None = the system default);
allowance_use records each listing generated and draft created with its time,
so a period is a window over them; app_setting holds the admin-set system
default. Separate from the Etsy request quota (tenant.daily_quota).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_allowance"
down_revision: str | None = "0029_seller_trademark_filter"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("allowance_amount", sa.Integer()))
    op.add_column("tenant", sa.Column("allowance_period", sa.Text()))
    op.create_table(
        "allowance_use",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_allowance_use_tenant_at", "allowance_use", ["tenant_id", "at"])
    op.create_table(
        "app_setting",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
    op.drop_index("ix_allowance_use_tenant_at", table_name="allowance_use")
    op.drop_table("allowance_use")
    op.drop_column("tenant", "allowance_period")
    op.drop_column("tenant", "allowance_amount")
