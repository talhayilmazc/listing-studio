"""listing_publication.manual_done: the seller's per-draft ticks for manual settings

Revision ID: 0017_publication_manual_done
Revises: 0016_multi_shop
Create Date: 2026-09-24

Some settings (Etsy's Creativity Standards question first) cannot be made through
the API, so each draft lists them for the seller. The seller ticks each one once
they have set it in Shop Manager. Ticks are stored as {field key: etsy_listing_id}
and count only for that draft, so a regenerated draft starts unticked.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_publication_manual_done"
down_revision: str | None = "0016_multi_shop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listing_publication",
        sa.Column(
            "manual_done",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("listing_publication", "manual_done")
