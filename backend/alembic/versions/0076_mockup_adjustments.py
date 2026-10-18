"""Each design adjusted on each mockup, and parts of it recoloured (render-time layers).

* ``mockup_adjustment``: a design on one photo's print area: where it sits relative to its
  automatic fit (move, scale, stretch, turn, free corners) and its look (opacity,
  brightness, contrast). Goes with the design or the photo.
* ``mockup_recolour``: regions of a design recoloured, per subgroup ("" = every subgroup).
  Goes with the design.

The uploaded design files are never changed. Nothing existing changes.

Revision ID: 0076_mockup_adjustments
Revises: 0075_draft_dismissal
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0076_mockup_adjustments"
down_revision: str | None = "0075_draft_dismissal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mockup_adjustment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_id", sa.Uuid(), sa.ForeignKey("mockup_design.id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", sa.Uuid(), sa.ForeignKey("mockup_photo.id", ondelete="CASCADE"), nullable=False),
        sa.Column("area", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("design_id", "photo_id", "area", name="uq_mockup_adjustment_place"),
    )
    op.create_index("ix_mockup_adjustment_tenant_id", "mockup_adjustment", ["tenant_id"])
    op.create_index("ix_mockup_adjustment_photo_id", "mockup_adjustment", ["photo_id"])
    op.create_table(
        "mockup_recolour",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_id", sa.Uuid(), sa.ForeignKey("mockup_design.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subgroup", sa.Text(), nullable=False, server_default=""),
        sa.Column("regions", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("design_id", "subgroup", name="uq_mockup_recolour_design_subgroup"),
    )
    op.create_index("ix_mockup_recolour_tenant_id", "mockup_recolour", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_mockup_recolour_tenant_id", table_name="mockup_recolour")
    op.drop_table("mockup_recolour")
    op.drop_index("ix_mockup_adjustment_photo_id", table_name="mockup_adjustment")
    op.drop_index("ix_mockup_adjustment_tenant_id", table_name="mockup_adjustment")
    op.drop_table("mockup_adjustment")
