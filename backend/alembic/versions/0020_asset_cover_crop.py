"""asset.cover_crop: the seller's square crop of a listing's cover photo

Revision ID: 0020_asset_cover_crop
Revises: 0019_scheduled_publishing
Create Date: 2026-09-25

The automatic square thumbnail cannot place lifestyle mockups well, so the
seller can pan and zoom the cover within a square. The crop is stored per asset
({x, y, size, width, height} in pixels of the processed image) and applied when
the draft is created and on "Replace images": the cropped square is uploaded
as the listing's first photo, since Etsy's API takes no crop parameters.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_asset_cover_crop"
down_revision: str | None = "0019_scheduled_publishing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("cover_crop", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("asset", "cover_crop")
