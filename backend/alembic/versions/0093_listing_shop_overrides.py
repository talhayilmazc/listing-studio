"""A listing's own profile and SKU in one shop ("Publish in another shop").

``generated_content.shop_overrides`` = ``{connection id: {"profile_id": ..., "sku": ...}}``.
A shop not in it, or a field not in its entry, follows the listing: its profile and its
group's SKU. Set when the seller sends listings already on Etsy in one shop to another shop
with that shop's profile and SKUs, or edits the SKU or profile while that shop is shown.
NULL for every existing listing: nothing changes for them.

Revision ID: 0093_listing_shop_overrides
Revises: 0092_etsy_reconcile
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093_listing_shop_overrides"
down_revision: str | None = "0092_etsy_reconcile"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("shop_overrides", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("generated_content", "shop_overrides")
