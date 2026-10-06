"""New profiles default to the "Etsy recommended (short)" listing style.

Existing profiles keep the style they have ("classic" = "Long keyword" for all
that never switched); only the column's default changes, so a profile created
from now on starts on "search". No row is rewritten.

Revision ID: 0050_short_title_default
Revises: 0049_drop_ad_spend
"""

from __future__ import annotations

from alembic import op

revision: str = "0050_short_title_default"
down_revision: str | None = "0049_drop_ad_spend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listing_profile") as batch:
        batch.alter_column("listing_style", server_default="search")


def downgrade() -> None:
    with op.batch_alter_table("listing_profile") as batch:
        batch.alter_column("listing_style", server_default="classic")
