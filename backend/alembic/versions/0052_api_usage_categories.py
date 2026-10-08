"""api_usage keeps what the day's requests were spent on, by category.

``categories`` = ``{"drafts": n, "publishing": n, ...}`` (etsy/categories.py),
the durable form of the 48-hour Redis hash. The per-draft request estimate is
the measured average from it (core/request_cost.py). Days recorded before this
have no breakdown (NULL) and are not used for the average.

Revision ID: 0052_api_usage_categories
Revises: 0051_listing_stats_and_versions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_api_usage_categories"
down_revision: str | None = "0051_listing_stats_and_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_usage", sa.Column("categories", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")))


def downgrade() -> None:
    op.drop_column("api_usage", "categories")
