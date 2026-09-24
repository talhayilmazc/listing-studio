"""listing_profile: separate image clock and refresh failures, for auto-refresh

Revision ID: 0018_profile_auto_refresh
Revises: 0017_publication_manual_done
Create Date: 2026-09-25

Profiles now refresh themselves before their limits run out (docs/duzeltmeler-v6.md
§H): the reference image links (6-hour display limit) every 5 hours with one
request, the structural data (24-hour limit) every 20 hours. The two need their
own clocks, so ``images_updated_at`` joins ``updated_at``. A refresh that fails
(the shop was disconnected, the reference listing is gone) is recorded so the
seller is told instead of finding a stale profile.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_profile_auto_refresh"
down_revision: str | None = "0017_publication_manual_done"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("images_updated_at", sa.DateTime(timezone=True)))
    op.add_column("listing_profile", sa.Column("refresh_error", sa.Text()))
    op.add_column("listing_profile", sa.Column("refresh_failed_at", sa.DateTime(timezone=True)))
    # Until now one refresh set both; the image links are as old as the rest.
    op.execute("UPDATE listing_profile SET images_updated_at = updated_at")


def downgrade() -> None:
    op.drop_column("listing_profile", "refresh_failed_at")
    op.drop_column("listing_profile", "refresh_error")
    op.drop_column("listing_profile", "images_updated_at")
