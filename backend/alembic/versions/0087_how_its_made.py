"""How it's made: Etsy's Creativity Standards answers on each profile, and Finish in Etsy.

``listing_profile.how_made_production`` (one of pipeline/how_made.PRODUCTION) and
``how_made_tools`` (a list of pipeline/how_made.TOOLS; "none" alone): the answers the seller
ticks in Etsy, since Etsy's API has no field for them. ``listing_publication.how_made_opened_at``:
when the seller last opened the listing in Etsy from "Finish in Etsy", so the app can watch
whether the draft went live and ask "Mark done?". Nothing existing changes: no profile has
answers until the seller sets them.

Revision ID: 0087_how_its_made
Revises: 0086_group_schedules
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0087_how_its_made"
down_revision: str | None = "0086_group_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("how_made_production", sa.Text(), nullable=True))
    op.add_column("listing_profile", sa.Column("how_made_tools", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))
    op.add_column("listing_publication", sa.Column("how_made_opened_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_publication", "how_made_opened_at")
    op.drop_column("listing_profile", "how_made_tools")
    op.drop_column("listing_profile", "how_made_production")
