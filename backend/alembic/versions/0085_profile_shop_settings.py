"""A profile's settings per shop, and a shop's daily limit (B7: the Shops table on a profile).

``listing_profile.shop_settings`` = ``{connection id: {"section_title": ..., "title_prefix": ...}}``:
in that shop the profile's listings default to that section (by title, the shop's own) and
use that title prefix (the main shop's prefix stays ``listing_profile.title_prefix``).
NULL or a missing shop: the shop follows the profile (no section default; the profile's
prefix). ``etsy_connection.daily_limit``: listings a day that go live in the shop when a
schedule spreads them out (NULL: the schedule's own number). Nothing existing changes.

Revision ID: 0085_profile_shop_settings
Revises: 0084_send_to
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0085_profile_shop_settings"
down_revision: str | None = "0084_send_to"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("shop_settings", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))
    op.add_column("etsy_connection", sa.Column("daily_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("etsy_connection", "daily_limit")
    op.drop_column("listing_profile", "shop_settings")
