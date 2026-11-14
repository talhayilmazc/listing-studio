"""Mockup render keys, sharpening and reuse (feature/mockups-editor B5-B7).

``mockup_output.render_key``: a hash of everything that decides a mockup's pixels; an unchanged mockup is not made
again (NULL for existing mockups: they are made again the first time, as before). ``mockup_run.sharpen``: the run's
output sharpening, "off" for every existing run (their mockups look as they do). ``mockup_run.reused``: how many
mockups of the last pass were kept as they were. The downgrade drops the three columns.

Revision ID: 0105_mockup_render_keys
Revises: 0104_mockup_placement_presets
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0105_mockup_render_keys"
down_revision: str | None = "0104_mockup_placement_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_output", sa.Column("render_key", sa.Text(), nullable=True))
    op.add_column("mockup_run", sa.Column("sharpen", sa.Text(), nullable=False, server_default="off"))
    op.add_column("mockup_run", sa.Column("reused", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("mockup_run", "reused")
    op.drop_column("mockup_run", "sharpen")
    op.drop_column("mockup_output", "render_key")
