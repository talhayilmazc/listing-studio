"""multi-shop: several Etsy shops per account (docs/duzeltmeler-v5.md §E)

Revision ID: 0016_multi_shop
Revises: 0015_job_paused_reason
Create Date: 2026-09-24

* etsy_connection gains a display name and an order, and at most one *active*
  connection may exist per Etsy user: a shop belongs to one account.
* tenant.max_shops: an admin's per-account override of MAX_SHOPS_PER_TENANT.
* listing_profile belongs to a shop (connection), not just the account. Existing
  profiles move to their account's most recent connection; a profile whose
  account never connected a shop cannot be used and is removed.
* shop_listing_cache rows carry their shop. It is a 6-hour cache that refills
  itself, so it is emptied rather than back-filled.
* listing_publication: one draft per (generated content, shop). Existing drafts
  are carried over from generated_content, attributed to the shop their publish
  job ran against, and the single-shop columns are dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_multi_shop"
down_revision: str | None = "0015_job_paused_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- connections -------------------------------------------------------------
    op.add_column("etsy_connection", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column(
        "etsy_connection",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_connection_active_etsy_user",
        "etsy_connection",
        ["etsy_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column("tenant", sa.Column("max_shops", sa.Integer(), nullable=True))

    # --- profiles belong to a shop -------------------------------------------------
    op.add_column("listing_profile", sa.Column("connection_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE listing_profile p
           SET connection_id = (
               SELECT c.id FROM etsy_connection c
                WHERE c.tenant_id = p.tenant_id
                ORDER BY (c.status = 'active') DESC, c.connected_at DESC
                LIMIT 1)
        """
    )
    op.execute("DELETE FROM listing_profile WHERE connection_id IS NULL")
    op.alter_column("listing_profile", "connection_id", nullable=False)
    op.create_foreign_key(
        "fk_listing_profile_connection",
        "listing_profile",
        "etsy_connection",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_listing_profile_connection", "listing_profile", ["connection_id"])

    # --- the shop listing cache: refills itself, so start empty -------------------
    op.execute("DELETE FROM shop_listing_cache")
    op.add_column("shop_listing_cache", sa.Column("connection_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_shop_listing_cache_connection",
        "shop_listing_cache",
        "etsy_connection",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_shop_listing_cache_connection_id", "shop_listing_cache", ["connection_id"]
    )

    # --- publications: one draft per content per shop ------------------------------
    op.create_table(
        "listing_publication",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_id",
            sa.Uuid(),
            sa.ForeignKey("generated_content.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Uuid(),
            sa.ForeignKey("listing_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("etsy_listing_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("content_id", "connection_id", name="uq_publication_content_shop"),
    )
    op.create_index("ix_publication_tenant", "listing_publication", ["tenant_id"])
    op.create_index(
        "ix_listing_publication_connection_id", "listing_publication", ["connection_id"]
    )
    # Each existing draft goes to the shop its (latest successful) create-draft job
    # ran against; failing that, the account's most recent connection.
    op.execute(
        """
        INSERT INTO listing_publication
            (id, tenant_id, content_id, connection_id, profile_id, etsy_listing_id, state)
        SELECT gen_random_uuid(), g.tenant_id, g.id,
               COALESCE(
                 (SELECT j.connection_id FROM job j
                   WHERE j.payload->>'content_id' = g.id::text
                     AND j.type = 'create_draft' AND j.status = 'succeeded'
                   ORDER BY j.finished_at DESC NULLS LAST LIMIT 1),
                 (SELECT c.id FROM etsy_connection c
                   WHERE c.tenant_id = g.tenant_id
                   ORDER BY c.connected_at DESC LIMIT 1)),
               g.listing_profile_id, g.etsy_listing_id,
               COALESCE(g.etsy_listing_state, 'draft')
          FROM generated_content g
         WHERE g.etsy_listing_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM etsy_connection c WHERE c.tenant_id = g.tenant_id)
        """
    )
    op.drop_column("generated_content", "etsy_listing_id")
    op.drop_column("generated_content", "etsy_listing_state")


def downgrade() -> None:
    op.add_column("generated_content", sa.Column("etsy_listing_state", sa.Text(), nullable=True))
    op.add_column("generated_content", sa.Column("etsy_listing_id", sa.BigInteger(), nullable=True))
    # One shop per content again: keep the earliest draft.
    op.execute(
        """
        UPDATE generated_content g
           SET etsy_listing_id = p.etsy_listing_id, etsy_listing_state = p.state
          FROM (SELECT DISTINCT ON (content_id) content_id, etsy_listing_id, state
                  FROM listing_publication ORDER BY content_id, created_at) p
         WHERE p.content_id = g.id
        """
    )
    op.drop_table("listing_publication")
    op.drop_index("ix_shop_listing_cache_connection_id", table_name="shop_listing_cache")
    op.drop_constraint(
        "fk_shop_listing_cache_connection", "shop_listing_cache", type_="foreignkey"
    )
    op.drop_column("shop_listing_cache", "connection_id")
    op.drop_index("ix_listing_profile_connection", table_name="listing_profile")
    op.drop_constraint("fk_listing_profile_connection", "listing_profile", type_="foreignkey")
    op.drop_column("listing_profile", "connection_id")
    op.drop_column("tenant", "max_shops")
    op.drop_index("uq_connection_active_etsy_user", table_name="etsy_connection")
    op.drop_column("etsy_connection", "position")
    op.drop_column("etsy_connection", "display_name")
