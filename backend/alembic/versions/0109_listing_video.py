"""A listing's video (feature/listing-video).

``listing_video``: the seller's own video file for a listing group of a batch (and optionally for that group in
one shop), received in pieces and read by the worker. ``listing_publication`` gains what became of the video on
each draft (``video_state``, ``video_error``, ``etsy_video_id``, ``video_sent_id``): all NULL for every existing
row, so a draft without a video is exactly as it was. Additive; the downgrade drops the table and the columns.

Revision ID: 0109_listing_video
Revises: 0108_manual_profiles
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0109_listing_video"
down_revision: str | None = "0108_manual_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_video",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("upload_folder", sa.Text(), nullable=True),
        sa.Column("placed", sa.Text(), nullable=False, server_default="slot"),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="receiving"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("file_key", sa.Text(), nullable=True),
        sa.Column("poster_key", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("has_audio", sa.Boolean(), nullable=True),
        sa.Column("files_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_listing_video_tenant_id", "listing_video", ["tenant_id"])
    op.create_index("ix_listing_video_batch_id", "listing_video", ["batch_id"])
    op.create_index("ix_listing_video_connection_id", "listing_video", ["connection_id"])
    op.create_index("ix_listing_video_batch_group", "listing_video", ["batch_id", "group_key"])
    op.add_column("listing_publication", sa.Column("video_state", sa.Text(), nullable=True))
    op.add_column("listing_publication", sa.Column("video_error", sa.Text(), nullable=True))
    op.add_column("listing_publication", sa.Column("etsy_video_id", sa.BigInteger(), nullable=True))
    op.add_column("listing_publication", sa.Column("video_sent_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_publication", "video_sent_id")
    op.drop_column("listing_publication", "etsy_video_id")
    op.drop_column("listing_publication", "video_error")
    op.drop_column("listing_publication", "video_state")
    op.drop_index("ix_listing_video_batch_group", table_name="listing_video")
    op.drop_index("ix_listing_video_connection_id", table_name="listing_video")
    op.drop_index("ix_listing_video_batch_id", table_name="listing_video")
    op.drop_index("ix_listing_video_tenant_id", table_name="listing_video")
    op.drop_table("listing_video")
