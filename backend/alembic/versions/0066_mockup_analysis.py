"""What the template analysis found (pipeline/mockup_vision.py).

``mockup_template``:

* ``occluder_key``: the hair, skin and accessories in front of the garment (a
  one-band PNG beside the blank), when the photo shows a person.
* ``occlusion``: print behind them (the seller can switch it off per template).
* ``print_area_confidence``: how sure the found print area is (0..1); below 0.6
  the card asks the seller to check it.
* ``analysis``: how it was found ("pose", "outline", "default"), the sentence the
  seller is shown, and the area found (for "Reset print area").

Templates made before this keep their print area; they have no occluder and no
confidence (the card does not ask them to check). Nothing is deleted.

Revision ID: 0066_mockup_analysis
Revises: 0065_mockup_sets
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066_mockup_analysis"
down_revision: str | None = "0065_mockup_sets"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("mockup_template") as table:
        table.add_column(sa.Column("occluder_key", sa.Text(), nullable=True))
        table.add_column(sa.Column("occlusion", sa.Boolean(), nullable=False, server_default=sa.true()))
        table.add_column(sa.Column("print_area_confidence", sa.Float(), nullable=True))
        table.add_column(sa.Column("analysis", JSONB, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mockup_template") as table:
        table.drop_column("analysis")
        table.drop_column("print_area_confidence")
        table.drop_column("occlusion")
        table.drop_column("occluder_key")
