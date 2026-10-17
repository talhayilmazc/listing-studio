"""How a seller places prints on their own garment photos (per account only).

``mockup_placement``: one row per garment of a photo the seller confirmed ("Looks right" or
"Confirm selected"): the garment's type and layout (``key``, e.g. "t-shirt worn") and the
confirmed print area's width, offset below the collar and aspect, relative to the garment's
chest width (pipeline/print_rule.py ``observe``). The account's next photos of that type
and layout are suggested from the median of its own rows (``personal_table``). Rows are
the seller's: deleted with the account (cascade) or by "Reset" on the Templates page; a
deleted photo leaves its numbers (``photo_id`` NULL). Never read for any other account.

Nothing existing changes.

Revision ID: 0070_mockup_placement
Revises: 0069_mockup_front_brush
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0070_mockup_placement"
down_revision: str | None = "0069_mockup_front_brush"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mockup_placement",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", sa.Uuid(), sa.ForeignKey("mockup_photo.id", ondelete="SET NULL"), nullable=True),
        sa.Column("garment", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("top", sa.Float(), nullable=False),
        sa.Column("aspect", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_placement_tenant_id", "mockup_placement", ["tenant_id"])
    op.create_index("ix_mockup_placement_photo_id", "mockup_placement", ["photo_id"])


def downgrade() -> None:
    op.drop_index("ix_mockup_placement_photo_id", table_name="mockup_placement")
    op.drop_index("ix_mockup_placement_tenant_id", table_name="mockup_placement")
    op.drop_table("mockup_placement")
