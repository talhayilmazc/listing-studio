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
    false,
    func,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EtsyConnection(Base):
    __tablename__ = "etsy_connection"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    etsy_user_id: Mapped[int | None] = mapped_column(BigInteger)
    shop_id: Mapped[int | None] = mapped_column(BigInteger)
    shop_name: Mapped[str | None] = mapped_column(Text)
    #: The app's shop section id, created lazily on first publish and reused.
    section_id: Mapped[int | None] = mapped_column(BigInteger)
    # Encrypted at rest (Fernet / AES-GCM). Never logged, never serialized.
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str] | None] = mapped_column(TEXT_ARRAY_TYPE)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    processed_key: Mapped[str | None] = mapped_column(Text)
    #: 1-based display order within the batch (Etsy listing image rank).
    rank: Mapped[int | None] = mapped_column(Integer)
    #: Last content-generation failure reason (safe text, no tokens); null when ok.
    error: Mapped[str | None] = mapped_column(Text)
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
    #: Set once this approved content has been published as an Etsy DRAFT listing.
    etsy_listing_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Etsy listing state after publishing: "draft" on create, "active" once the
    #: seller explicitly publishes it. Null == never published (treated as draft).
    etsy_listing_state: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ListingProfile(Base):
    """A product-type profile copied from one of the seller's own listings.

    The seller picks a reference listing; :attr:`cached_payload` holds the fields
    the app reuses verbatim (category, attributes, price, shipping, variation
    structure, description body, image ids). That payload is Member Content, so it
    is refreshed when older than :attr:`CACHE_MAX_AGE_SECONDS` (24h, CLAUDE.md) and
    deleted when the seller disconnects. Only the authenticated seller's own shop
    is ever read (CLAUDE.md constraint #2).
    """

    __tablename__ = "listing_profile"
    __table_args__ = (Index("ix_listing_profile_tenant", "tenant_id"),)

    #: Reference-content cache staleness window (Member Content, ToU §1).
    CACHE_MAX_AGE_SECONDS: ClassVar[int] = 24 * 3600

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    reference_listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Fields copied from the reference listing; null until first refresh.
    cached_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    #: Reference listing_image_ids always appended to new drafts (B3, e.g. size charts).
    fixed_image_ids: Mapped[list[int] | None] = mapped_column(JSONB_TYPE)
    #: How many leading description lines the generated title replaces (B2).
    title_replace_lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    #: Which content prompt to use: "apparel" | "digital_products" (C1).
    content_template: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="digital_products"
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
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
