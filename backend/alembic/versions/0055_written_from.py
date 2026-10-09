"""The cover a listing's title and tags were written from.

``generated_content.written_from_asset_id``: set when the text is written (and by
"Replace images" with new text). When the group's cover changes, ``asset_id``
moves to the new cover and the two differ, so the seller is told the title and
tags came from the old cover. Existing rows: their current cover.

Revision ID: 0055_written_from
Revises: 0054_grouping_mode
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0055_written_from"
down_revision: str | None = "0054_grouping_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("written_from_asset_id", sa.Uuid()))
    content = sa.table("generated_content", sa.column("written_from_asset_id", sa.Uuid()), sa.column("asset_id", sa.Uuid()))
    op.execute(content.update().values(written_from_asset_id=content.c.asset_id))


def downgrade() -> None:
    op.drop_column("generated_content", "written_from_asset_id")
