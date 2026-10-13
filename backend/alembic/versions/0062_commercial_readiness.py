"""Consent, account deletion and the invite form's shop count.

``tenant.terms_version`` / ``terms_accepted_at``: which version of the Terms of
Service and Privacy Policy the account agreed to, and when. Existing accounts
have neither, so each is asked once at its next sign-in before it can use the
app; a new version asks everyone again (core/terms.py).

``tenant.deletion_requested_at``: the seller asked for the account to be deleted
(Settings). Its shops are disconnected and their Etsy data deleted at once; a
job then deletes the uploads and the account itself (workers/accounts.py).

``invite_request.shop_count``: how many shops the person asking has (the form
asks; NULL on requests sent before this).

Nothing is deleted or rewritten by this migration.

Revision ID: 0062_commercial_readiness
Revises: 0061_manual_and_targets
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0062_commercial_readiness"
down_revision: str | None = "0061_manual_and_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("terms_version", sa.Text(), nullable=True))
    op.add_column("tenant", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenant", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invite_request", sa.Column("shop_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("invite_request", "shop_count")
    op.drop_column("tenant", "deletion_requested_at")
    op.drop_column("tenant", "terms_accepted_at")
    op.drop_column("tenant", "terms_version")
