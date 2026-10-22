"""Drop listing_publication.how_made_opened_at: "Finish in Etsy" is gone (2026-10-23).

0087 added it for the "Finish in Etsy" page, which watched whether a draft the seller opened
there went live. The owner's rule since: the seller does nothing per listing for Etsy's How it's
made questions, so that per-listing list and its watcher were removed. The profile's two answers
(``listing_profile.how_made_production``, ``how_made_tools``) stay: they are sent the day Etsy adds
the fields. The column only held a time; dropping it loses nothing a seller sees. The downgrade
adds it back, empty.

Revision ID: 0090_drop_how_made_opened
Revises: 0089_profile_merge_scrub
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0090_drop_how_made_opened"
down_revision: str | None = "0089_profile_merge_scrub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("listing_publication", "how_made_opened_at")


def downgrade() -> None:
    op.add_column("listing_publication", sa.Column("how_made_opened_at", sa.DateTime(timezone=True), nullable=True))
