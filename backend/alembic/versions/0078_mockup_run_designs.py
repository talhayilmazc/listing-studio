"""The designs a mockup run was made for.

* ``mockup_run.design_ids``: the designs the seller ticked before "Create mockups"; NULL (every
  run before this, and a run with nothing unticked) means every design of the batch.

Nothing existing changes.

Revision ID: 0078_mockup_run_designs
Revises: 0077_mockup_presets
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0078_mockup_run_designs"
down_revision: str | None = "0077_mockup_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_run", sa.Column("design_ids", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("mockup_run", "design_ids")
