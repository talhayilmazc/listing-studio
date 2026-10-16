"""The seller's "Keep in front" brush on a template photo, kept apart from the analysis's map.

``mockup_photo.occluder_auto_key``: what the analysis found in front of the garment.
``mockup_photo.occluder_manual_key``: the seller's brush (255 keep in front, 0 not in
front, 128 as found). ``occluder_key`` stays what every mockup and preview reads: the
two combined. Existing photos keep their map in ``occluder_key``; it is taken as the
analysis's own the first time the seller paints. Nothing is deleted.

Revision ID: 0069_mockup_front_brush
Revises: 0068_mockup_template_photos
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0069_mockup_front_brush"
down_revision: str | None = "0068_mockup_template_photos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_photo", sa.Column("occluder_auto_key", sa.Text(), nullable=True))
    op.add_column("mockup_photo", sa.Column("occluder_manual_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mockup_photo", "occluder_manual_key")
    op.drop_column("mockup_photo", "occluder_auto_key")
