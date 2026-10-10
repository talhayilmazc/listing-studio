"""The SKU on each content version (editable SKU, Part 5).

``content_version.sku``: the SKU the draft's products carry (their base; per-size
SKUs keep the profile's pattern). Written with the draft, and a new version is
started when the seller updates the SKU on Etsy (reason "sku_edited").

Revision ID: 0058_version_sku
Revises: 0057_item_options
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0058_version_sku"
down_revision: str | None = "0057_item_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_version", sa.Column("sku", sa.Text()))


def downgrade() -> None:
    op.drop_column("content_version", "sku")
