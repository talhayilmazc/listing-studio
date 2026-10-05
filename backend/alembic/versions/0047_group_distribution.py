"""Shop groups at work (v8 §B): where each design went, and group schedules

* ``design_distribution``: which group a design was sent to (warned about, not
  blocked, if it is sent to another group later).
* ``group_plan`` / ``planned_slot``: a confirmed group schedule and its listing x
  shop x time slots; a cron creates each draft at its time and schedules its
  go-live for the time the seller confirmed.

Revision ID: 0047_group_distribution
Revises: 0046_profile_links
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_group_distribution"
down_revision: str | None = "0046_profile_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _uuid(name: str, *args, **kw) -> sa.Column:  # noqa: ANN001, ANN002, ANN003
    return sa.Column(name, sa.Uuid(), *args, **kw)


def upgrade() -> None:
    op.create_table(
        "design_distribution",
        _uuid("id", primary_key=True),
        _uuid("tenant_id", sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid("content_id", sa.ForeignKey("generated_content.id", ondelete="SET NULL"), index=True),
        sa.Column("sku", sa.Text(), index=True),
        _uuid("group_id", sa.ForeignKey("shop_group.id", ondelete="SET NULL")),
        sa.Column("group_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "group_plan",
        _uuid("id", primary_key=True),
        _uuid("tenant_id", sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid("batch_id", sa.ForeignKey("upload_batch.id", ondelete="SET NULL")),
        sa.Column("settings", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "planned_slot",
        _uuid("id", primary_key=True),
        _uuid("tenant_id", sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid("plan_id", sa.ForeignKey("group_plan.id", ondelete="CASCADE"), nullable=False, index=True),
        _uuid("content_id", sa.ForeignKey("generated_content.id", ondelete="SET NULL"), index=True),
        _uuid("connection_id", sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("draft_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="waiting"),
        _uuid("job_id", sa.ForeignKey("job.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text()),
    )
    op.create_index("ix_planned_slot_due", "planned_slot", ["state", "draft_at"])


def downgrade() -> None:
    op.drop_index("ix_planned_slot_due", table_name="planned_slot")
    op.drop_table("planned_slot")
    op.drop_table("group_plan")
    op.drop_table("design_distribution")
