"""listing_publication: scheduled publishing (v6 §G)

Revision ID: 0019_scheduled_publishing
Revises: 0018_profile_auto_refresh
Create Date: 2026-09-25

A seller can choose when an approved listing's draft goes live. The schedule is
the seller's explicit confirmation (CLAUDE.md rule 3): only a draft of a listing
they approved one by one can be scheduled, and nothing they did not schedule is
published. The time is stored in UTC; the job released at that time is kept so
the seller can see whether it is waiting for the daily budget, done or failed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_scheduled_publishing"
down_revision: str | None = "0018_profile_auto_refresh"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_publication", sa.Column("scheduled_for", sa.DateTime(timezone=True)))
    op.add_column(
        "listing_publication",
        sa.Column(
            "schedule_job_id",
            sa.Uuid(),
            sa.ForeignKey("job.id", ondelete="SET NULL", name="fk_publication_schedule_job"),
        ),
    )
    op.add_column("listing_publication", sa.Column("schedule_note", sa.Text()))
    op.create_index(
        "ix_listing_publication_scheduled_for", "listing_publication", ["scheduled_for"]
    )


def downgrade() -> None:
    op.drop_index("ix_listing_publication_scheduled_for", table_name="listing_publication")
    op.drop_column("listing_publication", "schedule_note")
    op.drop_constraint("fk_publication_schedule_job", "listing_publication", type_="foreignkey")
    op.drop_column("listing_publication", "schedule_job_id")
    op.drop_column("listing_publication", "scheduled_for")
