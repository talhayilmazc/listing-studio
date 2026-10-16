"""Every garment in a template photo, its front/back view, and how a run uses them.

``mockup_template``:

* ``view``: "front" or "back" (found from the pose; the seller can change it). A run
  can print on front templates, back templates or both.
* ``garments``: every garment found in the photo, in reading order:
  ``[{"print_area", "confidence", "kind", "view", "layout", "source"}]``. The first
  is the template's ``print_area``. NULL for a single garment found by the earlier
  analysis, and for templates made before this.

``mockup_run``:

* ``sides``: "front", "back" or "both": which of the chosen templates it prints on.
* ``garment_mode``: "same" (the same design on every garment of a photo) or "order"
  (one design per garment, in order).

Existing templates are front views with one garment; existing runs printed on every
template with one design per mockup. Nothing is deleted.

Revision ID: 0067_mockup_garments
Revises: 0066_mockup_analysis
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0067_mockup_garments"
down_revision: str | None = "0066_mockup_analysis"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("mockup_template") as table:
        table.add_column(sa.Column("view", sa.Text(), nullable=False, server_default="front"))
        table.add_column(sa.Column("garments", JSONB, nullable=True))
    with op.batch_alter_table("mockup_run") as table:
        table.add_column(sa.Column("sides", sa.Text(), nullable=False, server_default="both"))
        table.add_column(sa.Column("garment_mode", sa.Text(), nullable=False, server_default="same"))


def downgrade() -> None:
    with op.batch_alter_table("mockup_run") as table:
        table.drop_column("garment_mode")
        table.drop_column("sides")
    with op.batch_alter_table("mockup_template") as table:
        table.drop_column("garments")
        table.drop_column("view")
