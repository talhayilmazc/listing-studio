"""What was written to an existing listing's personalization on Etsy (WIP, 2026-10-26).

``listing_publication.personalization_sync``: the last time the seller had personalization
written to this existing draft or live listing (state "queued", "written" or "failed", when,
why); NULL when never asked. The downgrade drops it.

Revision ID: 0093_personalization_sync
Revises: 0092_etsy_reconcile
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093_personalization_sync"
down_revision: str | None = "0092_etsy_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_publication", sa.Column(
        "personalization_sync", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_publication", "personalization_sync")
