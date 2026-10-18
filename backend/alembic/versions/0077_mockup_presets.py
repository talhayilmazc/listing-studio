"""Subgroup presets per account, a file-name prefix per template, "Print on all garments" per photo.

* ``mockup_subgroup_preset``: an account's subgroups defined once (name, suffix, use on) that
  any template can take.
* ``mockup_template.file_prefix``: downloaded mockups and listing photos named
  "<prefix>-<SKU>-<NN>.jpg"; NULL keeps today's naming.
* ``mockup_photo.print_all``: the same design on every area of the photo (false: as the run says).

Nothing existing changes.

Revision ID: 0077_mockup_presets
Revises: 0076_mockup_adjustments
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0077_mockup_presets"
down_revision: str | None = "0076_mockup_adjustments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mockup_subgroup_preset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("use_on", sa.Text(), nullable=False, server_default="any"),
        sa.Column("suffix", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_mockup_subgroup_preset_name"),
    )
    op.create_index("ix_mockup_subgroup_preset_tenant_id", "mockup_subgroup_preset", ["tenant_id"])
    op.add_column("mockup_template", sa.Column("file_prefix", sa.Text(), nullable=True))
    op.add_column("mockup_photo", sa.Column("print_all", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("mockup_photo", "print_all")
    op.drop_column("mockup_template", "file_prefix")
    op.drop_index("ix_mockup_subgroup_preset_tenant_id", table_name="mockup_subgroup_preset")
    op.drop_table("mockup_subgroup_preset")
