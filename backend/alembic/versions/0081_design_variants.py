"""Light and dark variants of one design, and each template photo's colour group.

* ``mockup_photo.colour`` / ``colour_lightness`` / ``colour_source``: "light" or "dark",
  measured from the garment or set by the seller. NULL until measured (new photos are
  measured when prepared; existing ones by ``python -m app.cli mockup-colours --apply`` or
  the first time they are used).
* ``mockup_design.variant`` / ``fill_other`` and ``mockup_design_version.variant``: a file's
  variant ("-white" -> light, "-dark" -> dark); one design, one listing.
* ``mockup_design_version.subgroup`` becomes nullable: a variant file is not for a
  seller-defined subgroup.
* ``tenant.variant_suffix`` ("shirt" / "ink") and ``mockup_run.variant_suffix``.

Nothing existing changes: every existing design, version, run, listing and draft keeps
what it had (no variant, the shirt meaning).

Revision ID: 0081_design_variants
Revises: 0080_shop_group_members
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0081_design_variants"
down_revision: str | None = "0080_shop_group_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_photo", sa.Column("colour", sa.Text(), nullable=True))
    op.add_column("mockup_photo", sa.Column("colour_lightness", sa.Float(), nullable=True))
    op.add_column("mockup_photo", sa.Column("colour_source", sa.Text(), nullable=True))
    op.add_column("mockup_design", sa.Column("variant", sa.Text(), nullable=True))
    op.add_column("mockup_design", sa.Column("fill_other", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("mockup_design_version", sa.Column("variant", sa.Text(), nullable=True))
    op.alter_column("mockup_design_version", "subgroup", existing_type=sa.Text(), nullable=True)
    op.add_column("tenant", sa.Column("variant_suffix", sa.Text(), nullable=False, server_default="shirt"))
    op.add_column("mockup_run", sa.Column("variant_suffix", sa.Text(), nullable=False, server_default="shirt"))


def downgrade() -> None:
    op.drop_column("mockup_run", "variant_suffix")
    op.drop_column("tenant", "variant_suffix")
    # Variant files made after the upgrade have no subgroup; they go with the downgrade.
    op.execute("DELETE FROM mockup_design_version WHERE subgroup IS NULL")
    op.alter_column("mockup_design_version", "subgroup", existing_type=sa.Text(), nullable=False)
    op.drop_column("mockup_design_version", "variant")
    op.drop_column("mockup_design", "fill_other")
    op.drop_column("mockup_design", "variant")
    op.drop_column("mockup_photo", "colour_source")
    op.drop_column("mockup_photo", "colour_lightness")
    op.drop_column("mockup_photo", "colour")
