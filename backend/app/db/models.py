"""SQLAlchemy ORM models and enums.

Specification: ``docs/data-model.md``.

Member Content tables carry a retention strategy (CLAUDE.md). In particular
``listing_snapshot`` rows are deleted 90 days after ``taken_at`` by a periodic
cleanup job (implemented in a later work-order step); the age threshold lives
here as :attr:`ListingSnapshot.RETENTION_DAYS`.

Column types are written to be portable: native Postgres types in production
(``JSONB``, ``text[]``, ``uuid``, ``bytea``, ``timestamptz``) and JSON/compatible
types on SQLite so the model layer can be unit-tested without a database server.
Etsy client / OAuth / pipeline code is intentionally absent at this step.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base

# --- Portable column types --------------------------------------------------
# Default type is the SQLite-friendly one; the Postgres variant is used in prod.
JSONB_TYPE = JSON().with_variant(JSONB(), "postgresql")
TEXT_ARRAY_TYPE = JSON().with_variant(ARRAY(Text()), "postgresql")


# --- Enums ------------------------------------------------------------------
class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class ConnectionStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"


class JobType(str, enum.Enum):
    create_draft = "create_draft"
    update_listing = "update_listing"
    upload_image = "upload_image"
    sync_listings = "sync_listings"
    update_inventory = "update_inventory"
    refresh_profile = "refresh_profile"
    sync_shop_listings = "sync_shop_listings"
    publish_live = "publish_live"
    replace_images = "replace_images"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class AssetStatus(str, enum.Enum):
    uploaded = "uploaded"
    processed = "processed"
    failed = "failed"


class UploadBatchStatus(str, enum.Enum):
    uploading = "uploading"
    processing = "processing"
    ready = "ready"
    applied = "applied"
    failed = "failed"


class ComplianceSeverity(str, enum.Enum):
    blocking = "blocking"
    warning = "warning"
    info = "info"


def _enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """Build a named SQLAlchemy Enum that stores the members' string values."""
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)


# --- Models -----------------------------------------------------------------
class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        _enum(TenantStatus, "tenant_status"),
        nullable=False,
        default=TenantStatus.active,
    )
    daily_quota: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2000")
    # Set when an admin issues a temporary password: the tenant must replace it
    # before the rest of the API will answer (production-spec A4).
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    # Operator access to /admin. Granted only by the CLI (app.cli create-admin);
    # no endpoint can set it, so no request can escalate itself.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # How many Etsy shops this account may connect. Null = MAX_SHOPS_PER_TENANT;
    # an admin can set it per account (docs/duzeltmeler-v5.md §E).
    max_shops: Mapped[int | None] = mapped_column(Integer)
    # The trademark filter for this account (v7 §A4): None follows TRADEMARK_FILTER;
    # an admin can turn it on or off per account. Off, brand names may appear in
    # this seller's listings, at the seller's own risk under Etsy's IP policy.
    trademark_filter: Mapped[bool | None] = mapped_column(Boolean)
    # The seller's own costs for profit figures (v7 §C2): Etsy fee rates, product,
    # shipping and fixed costs. Editable, since fee rates change.
    cost_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    # Features an admin turned on for this account (v7 §B), e.g. {"own_patterns": true}.
    features: Mapped[dict[str, Any]] = mapped_column(
        JSONB_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class InviteCode(Base):
    """A single-use registration code (production-spec A1).

    Registration is invite-only: there is no public sign-up. A code is handed to
    the user out of band, and is spent the first time it is redeemed.
    """

    __tablename__ = "invite_code"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)  # who it was meant for
    used_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Optional: only this address may redeem the code. Stored lower-cased.
    bound_email: Mapped[str | None] = mapped_column(Text)
    # Set when an admin withdraws an unused code; a revoked code never redeems.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL")
    )


class AuditLog(Base):
    """Who did what to whom, and when — every admin action (production-spec admin).

    Rows name people by id only. Emails are joined in at read time, so the log
    holds no copy of anyone's address and deleting an account leaves a row that
    reads "deleted account" rather than a stale personal detail.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Null actor = the server CLI (bootstrap), not a signed-in admin.
    actor_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), index=True
    )
    target_invite_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invite_code.id", ondelete="SET NULL")
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EtsyConnection(Base):
    __tablename__ = "etsy_connection"
    __table_args__ = (
        # A shop (one Etsy user) belongs to one account at a time (v5 §E isolation).
        Index(
            "uq_connection_active_etsy_user",
            "etsy_user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    etsy_user_id: Mapped[int | None] = mapped_column(BigInteger)
    shop_id: Mapped[int | None] = mapped_column(BigInteger)
    shop_name: Mapped[str | None] = mapped_column(Text)
    #: The seller's own label for this shop in the shop switcher; falls back to shop_name.
    display_name: Mapped[str | None] = mapped_column(Text)
    #: Order in the shop switcher (ascending).
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The app's shop section id, created lazily on first publish and reused.
    section_id: Mapped[int | None] = mapped_column(BigInteger)
    # Encrypted at rest (Fernet / AES-GCM). Never logged, never serialized.
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str] | None] = mapped_column(TEXT_ARRAY_TYPE)
    #: When this shop's sales totals were last read (v7 §C1); None before the first read.
    sales_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ConnectionStatus] = mapped_column(
        _enum(ConnectionStatus, "connection_status"),
        nullable=False,
        default=ConnectionStatus.active,
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        # Token columns are deliberately excluded so they cannot leak via repr/logs.
        return (
            f"<EtsyConnection id={self.id!r} tenant_id={self.tenant_id!r} "
            f"shop_id={self.shop_id!r} status={self.status!r}>"
        )


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_tenant_status", "tenant_id", "status"),
        Index("ix_job_status_scheduled", "status", "scheduled_at"),
        Index("ix_job_batch", "batch_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[JobType] = mapped_column(_enum(JobType, "job_type"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, default=JobStatus.queued
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    last_error: Mapped[str | None] = mapped_column(Text)  # never contains tokens
    # Set while a queued job waits for the daily reset (PAUSE_GLOBAL / PAUSE_TENANT);
    # scheduled_at then holds when it resumes. Cleared when the job starts.
    paused_reason: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid())
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiUsage(Base):
    __tablename__ = "api_usage"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ListingSnapshot(Base):
    """Pre-change copy of a listing, used for rollback.

    Contains Member Content, so it is subject to retention: rows older than
    :attr:`RETENTION_DAYS` (measured from ``taken_at``) are deleted by a periodic
    cleanup job. This threshold comes from CLAUDE.md and supersedes any other
    spec if they disagree.
    """

    __tablename__ = "listing_snapshot"
    __table_args__ = (
        Index("ix_snapshot_tenant_listing_taken", "tenant_id", "listing_id", "taken_at"),
        Index("ix_snapshot_taken_at", "taken_at"),  # supports retention sweeps
    )

    #: Member Content retention window in days (CLAUDE.md).
    RETENTION_DAYS: ClassVar[int] = 90

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UploadBatch(Base):
    __tablename__ = "upload_batch"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[UploadBatchStatus] = mapped_column(
        _enum(UploadBatchStatus, "upload_batch_status"),
        nullable=False,
        default=UploadBatchStatus.uploading,
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Optional profile whose size-chart (fixed) images to append when publishing
    #: this batch's listings — lets size charts come from a different profile than
    #: the one supplying metadata (Task 4). Null = use each content's own profile.
    size_chart_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ListingGroupSetting(Base):
    """Per-folder-group profile selection within a batch (v4 §E).

    One upload can mix product types (e.g. 3 Comfort Colors + 2 standard tees), so
    the metadata profile and the size-chart profile are chosen per listing group,
    not just per batch. ``manual`` records that the seller set this group explicitly
    so a later bulk "apply to all" does not overwrite it.
    """

    __tablename__ = "listing_group_setting"
    __table_args__ = (
        Index("ix_group_setting_batch_group", "batch_id", "group_key", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_key: Mapped[str] = mapped_column(Text, nullable=False)  # "" is the root group
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL")
    )
    size_chart_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL")
    )
    #: Set by the seller directly (a bulk apply-to-all won't overwrite it).
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    #: One of the seller's OWN listings whose title and tag pattern this group's
    #: listing follows (v7 §B). Only the id is kept; its text is read from the
    #: shop's own 6-hour listing cache when content is generated.
    pattern_listing_id: Mapped[int | None] = mapped_column(BigInteger)


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = _uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_sku: Mapped[str | None] = mapped_column(Text)
    #: Folder-derived listing group (D1): one folder = one listing. Assets sharing
    #: a group_key are one listing's images; "" is the root (single) group; null is
    #: an ungrouped legacy asset. SKU is parsed from the folder name (D2).
    group_key: Mapped[str | None] = mapped_column(Text, index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Size of the uploaded original, for the per-batch ceiling (production-spec D).
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    processed_key: Mapped[str | None] = mapped_column(Text)
    #: 1-based display order within the batch (Etsy listing image rank).
    rank: Mapped[int | None] = mapped_column(Integer)
    #: Last content-generation failure reason (safe text, no tokens); null when ok.
    error: Mapped[str | None] = mapped_column(Text)
    #: The seller's square crop for when this image is a listing's cover:
    #: {x, y, size, width, height} in pixels of the processed image. Etsy's API
    #: takes no crop, so the cropped square is what is uploaded as photo 1.
    cover_crop: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    status: Mapped[AssetStatus] = mapped_column(
        _enum(AssetStatus, "asset_status"), nullable=False, default=AssetStatus.uploaded
    )


class GeneratedContent(Base):
    __tablename__ = "generated_content"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(TEXT_ARRAY_TYPE)
    description: Mapped[str | None] = mapped_column(Text)
    taxonomy_id: Mapped[int | None] = mapped_column(BigInteger)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    model_used: Mapped[str | None] = mapped_column(Text)
    # Required for per-listing cost measurement.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    #: The reference-listing profile that drives category/price/variations/
    #: description for this content (required by the generate path). SET NULL on
    #: profile delete so content survives.
    listing_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ListingPublication(Base):
    """One Etsy draft made from one piece of generated content, in one shop.

    Publishing the same content to N shops makes N drafts (docs/duzeltmeler-v5.md
    §E), each with its own row. Deleted when that shop is disconnected; the
    generated content itself is the seller's work and stays.
    """

    __tablename__ = "listing_publication"
    __table_args__ = (
        UniqueConstraint("content_id", "connection_id", name="uq_publication_content_shop"),
        Index("ix_publication_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_content.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The profile of *that* shop the draft was built from.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL")
    )
    etsy_listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: "draft" on create; "active" once the seller explicitly publishes it.
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    #: Settings the seller confirmed making by hand in Shop Manager (etsy/manual_fields.py),
    #: as {field key: the etsy_listing_id it was confirmed for}. A tick counts only
    #: for that draft, so a regenerated draft starts unticked.
    manual_done: Mapped[dict[str, Any]] = mapped_column(
        JSONB_TYPE, nullable=False, default=dict, server_default=text("'{}'")
    )
    #: When the seller chose for this draft to go live (UTC), v6 §G. Setting it is
    #: the seller's explicit confirmation (CLAUDE.md rule 3); only an approved
    #: listing's existing draft can be scheduled. Cleared when cancelled.
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    #: The publish-live job the schedule released at its time; None while waiting.
    schedule_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job.id", ondelete="SET NULL")
    )
    #: Why a due schedule did not publish (no longer approved, compliance), for the seller.
    schedule_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def manual_done_keys(self) -> set[str]:
        """Settings ticked for *this* draft (ticks for an earlier draft do not count)."""
        return {
            key
            for key, listing_id in (self.manual_done or {}).items()
            if listing_id == self.etsy_listing_id
        }


class ListingProfile(Base):
    """A product-type profile copied from one of the seller's own listings.

    The seller picks a reference listing; :attr:`cached_payload` holds the fields
    the app reuses verbatim (category, attributes, price, shipping, variation
    structure, description body, image ids). That payload is Member Content and
    holds two kinds of data under two limits (CLAUDE.md, ToU §1):

    * **displayed** — the reference image links shown in the profile card follow
      the listing-display limit, :attr:`DISPLAY_MAX_AGE_SECONDS` (6h). Past it the
      API stops returning them and retention strips them from storage.
    * **structural** — taxonomy, attributes, price, shipping, variation shape,
      readiness state, the description used to write each draft, and the images'
      ids, ranks and size-chart classifications. Never displayed, held to provide
      the service: :attr:`CACHE_MAX_AGE_SECONDS` (24h), then cleared.

    Everything is deleted when the seller disconnects. Only the authenticated
    seller's own shop is ever read (CLAUDE.md constraint #2).
    """

    __tablename__ = "listing_profile"
    __table_args__ = (
        Index("ix_listing_profile_tenant", "tenant_id"),
        Index("ix_listing_profile_connection", "connection_id"),
    )

    #: Structural reference data: held to provide the service (ToU §1).
    CACHE_MAX_AGE_SECONDS: ClassVar[int] = 24 * 3600
    #: Displayed reference data (image links): the listing-display limit.
    DISPLAY_MAX_AGE_SECONDS: ClassVar[int] = 6 * 3600
    #: Payload keys on each image entry that exist only to display it.
    DISPLAY_IMAGE_KEYS: ClassVar[tuple[str, ...]] = ("url", "display_url")

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: The shop this profile belongs to: each shop has its own reference listings
    #: (docs/duzeltmeler-v5.md §E). Deleted with that shop's Etsy data on disconnect.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    reference_listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Fields copied from the reference listing; null until first refresh.
    cached_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    #: Reference listing_image_ids always appended to new drafts (B3, e.g. size
    #: charts). Auto-detected by classifying the reference images; user-toggleable.
    fixed_image_ids: Mapped[list[int] | None] = mapped_column(JSONB_TYPE)
    #: Which content prompt to use: "apparel" | "digital_products" (C1). Apparel is
    #: the default; digital_products is opt-in.
    content_template: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="apparel"
    )
    #: The seller's personalization override (v7 §D4): None copies the reference's
    #: question; {"enabled": false} turns it off; otherwise the question to use.
    personalization: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    #: Fixed prefix prepended to every generated title (e.g. "COMFORT COLORS").
    #: Auto-filled from the reference title's leading words; editable. Null = not yet
    #: derived; "" = deliberately none.
    title_prefix: Mapped[str | None] = mapped_column(Text)
    #: How the profile was created: "manual" | "detected" (auto-clustered).
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    #: Auto-detected profiles start unconfirmed; the seller confirms/renames them
    #: before use (never used silently). Manual creates are confirmed on creation.
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    #: When the structural payload was last fetched (24-hour limit).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the image links were last fetched (6-hour display limit). Auto-refresh
    #: renews them on their own, more often than the rest (v6 §H).
    images_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why the last refresh failed, worded for the seller; cleared by a success.
    refresh_error: Mapped[str | None] = mapped_column(Text)
    refresh_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Auto-refresh renews each clock this long after it was set, ahead of its limit.
    AUTO_REFRESH_IMAGES_SECONDS: ClassVar[int] = 5 * 3600
    AUTO_REFRESH_SECONDS: ClassVar[int] = 20 * 3600
    #: After a failed refresh, wait this long before auto-refresh tries again.
    AUTO_REFRESH_RETRY_SECONDS: ClassVar[int] = 3 * 3600
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ShopListingCache(Base):
    """Cache of the seller's own existing listings for the dashboard (B4).

    Member Content: listing content, so rows are refetched when older than
    :attr:`STALE_SECONDS` (6h, CLAUDE.md) and deleted on disconnect.
    """

    __tablename__ = "shop_listing_cache"

    #: Listing-content staleness window (CLAUDE.md: 6h for listing content).
    STALE_SECONDS: ClassVar[int] = 6 * 3600

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: The shop the listing is in.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SalesDaily(Base):
    """One row per listing per day: units sold, orders and revenue (v7 §C1).

    Our own metric over the seller's own shop, derived from getShopReceiptTransactionsByShop:
    only listing id, quantity, price and date are read, the raw response is
    discarded in the same job, and nothing about buyers is ever stored. Kept
    :attr:`RETENTION_DAYS` (13 months, so a Christmas design's history is there
    the next November), then deleted; deleted at once when the shop disconnects.
    """

    __tablename__ = "sales_daily"
    __table_args__ = (Index("ix_sales_daily_tenant_day", "tenant_id", "day"),)

    RETENTION_DAYS: ClassVar[int] = 396

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    orders: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: In the shop's currency, in minor units (cents).
    revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    currency: Mapped[str | None] = mapped_column(Text)


class AdSpend(Base):
    """Etsy Ads spend the seller uploaded as CSV, per listing and period (v7 §C1).

    The Open API has no Ads data; the seller exports it from Shop Manager and
    maps its columns here. Kept 13 months like sales; deleted with the shop.
    """

    __tablename__ = "ad_spend"
    __table_args__ = (Index("ix_ad_spend_tenant_period", "tenant_id", "period_end"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    ad_orders: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ad_revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: Views the listing's ad got, when the report has them (judging new listings).
    ad_views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComplianceFinding(Base):
    __tablename__ = "compliance_finding"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    generated_content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_content.id", ondelete="CASCADE"), nullable=False, index=True
    )
    severity: Mapped[ComplianceSeverity] = mapped_column(
        _enum(ComplianceSeverity, "compliance_severity"), nullable=False
    )
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
