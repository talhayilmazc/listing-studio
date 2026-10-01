"""upload_batch.connection_id, listing_group_setting.connection_id: the shop is chosen, not implied

Revision ID: 0035_group_shop
Revises: 0034_draft_attempt
Create Date: 2026-10-02

Until now a group's shop was whichever shop its profile happened to belong to.
The shop becomes a stored choice of the batch and of each group. Existing rows
take the shop their profile already implied; a batch takes its groups' shop
when they all agree.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_group_shop"
down_revision: str | None = "0034_draft_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("upload_batch", sa.Column(
        "connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="SET NULL"), nullable=True))
    op.add_column("listing_group_setting", sa.Column(
        "connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="SET NULL"), nullable=True))
    op.execute(
        "UPDATE listing_group_setting SET connection_id = "
        "(SELECT p.connection_id FROM listing_profile p WHERE p.id = listing_group_setting.profile_id) "
        "WHERE profile_id IS NOT NULL"
    )
    op.execute(
        "UPDATE upload_batch SET connection_id = "
        "(SELECT MIN(CAST(g.connection_id AS TEXT)) FROM listing_group_setting g "
        " WHERE g.batch_id = upload_batch.id AND g.connection_id IS NOT NULL)::uuid "
        "WHERE (SELECT COUNT(DISTINCT g.connection_id) FROM listing_group_setting g "
        "       WHERE g.batch_id = upload_batch.id AND g.connection_id IS NOT NULL) = 1"
    )


def downgrade() -> None:
    op.drop_column("listing_group_setting", "connection_id")
    op.drop_column("upload_batch", "connection_id")
