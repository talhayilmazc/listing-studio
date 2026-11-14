"""Placement presets per mockup template (feature/mockups-editor B4): ``mockup_placement_preset``.

A named placement on one template ("Chest print", "Full front", "Left pocket"), saved by the seller and applied
with one click. New table only; nothing existing changes. The downgrade drops it.

Revision ID: 0104_mockup_placement_presets
Revises: 0103_mockup_downloads
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0104_mockup_placement_presets"
down_revision: str | None = "0103_mockup_downloads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mockup_placement_preset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("mockup_template.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("template_id", "name", name="uq_mockup_placement_preset_name"),
    )
    op.create_index("ix_mockup_placement_preset_tenant_id", "mockup_placement_preset", ["tenant_id"])
    op.create_index("ix_mockup_placement_preset_template_id", "mockup_placement_preset", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_mockup_placement_preset_template_id", table_name="mockup_placement_preset")
    op.drop_index("ix_mockup_placement_preset_tenant_id", table_name="mockup_placement_preset")
    op.drop_table("mockup_placement_preset")
