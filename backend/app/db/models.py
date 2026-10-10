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
from decimal import Decimal
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
    Numeric,
    Text,
    UniqueConstraint,
    false,
    true,
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
    #: The account's own Etsy requests per day, when an admin set one. NULL: the
    #: account follows the default (``ACCOUNT_DAILY_CEILING``). Never read this
    #: directly to decide a limit: ``core/limits.py::ceiling_limit`` does.
    #: An admin's cap on this account's stored image files, in bytes; None follows
    #: the default (``STORAGE_CAP_GB``; core/storage_cap.py).
    storage_cap_bytes: Mapped[int | None] = mapped_column(BigInteger)
    etsy_ceiling_override: Mapped[int | None] = mapped_column(Integer)
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
    # The trademark filter (v7 §A4). The seller's own choice, on by default,
    # changed in Settings (turning it off needs their explicit acceptance of the
    # risk under Etsy's IP policy); and an admin override that wins over it.
    # Both changes are audited. compliance/trademarks.filter_on decides.
    trademark_filter_seller: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    trademark_filter_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Admin override: None = the seller's own choice.
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
    #: The seller's IANA time zone ("America/Chicago"): schedules are entered and
    #: shown in it (app/core/timezones.py). Detected from the browser on first
    #: sign-in; editable in Settings. None until then.
    time_zone: Mapped[str | None] = mapped_column(Text)
    #: The product allowance (core/allowance.py): listings generated plus drafts
    #: created per period. None = the system default. Not the Etsy request quota.
    allowance_amount: Mapped[int | None] = mapped_column(Integer)
    #: "daily" | "weekly" | "monthly"; None = the system default.
    allowance_period: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AllowanceUse(Base):
    """One unit of the product allowance used: a listing generated or a draft created.

    Recorded as events with their time, so an allowance counts whatever falls in
    its current period: changing a seller's amount or period applies at once
    without losing what they have used. Kept 400 days, then deleted.
    """

    __tablename__ = "allowance_use"
    __table_args__ = (Index("ix_allowance_use_tenant_at", "tenant_id", "at"),)

    RETENTION_DAYS: ClassVar[int] = 400

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: "generation" (a listing's text written) or "draft" (a draft made on Etsy).
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    """App-wide settings an admin changes in the panel (e.g. the default allowance)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False)
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


class InviteRequest(Base):
    """What a visitor typed into the public "Request an invite" form.

    Personal data from someone who is not yet a user, so it is kept short and
    not kept long: a decided request is deleted after ``DECIDED_RETENTION_DAYS``,
    one nobody decided after ``PENDING_RETENTION_DAYS`` (workers/retention.py).
    The sender's IP address is never stored.
    """

    __tablename__ = "invite_request"
    DECIDED_RETENTION_DAYS: ClassVar[int] = 90
    PENDING_RETENTION_DAYS: ClassVar[int] = 180

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # lower-cased
    shop: Mapped[str | None] = mapped_column(Text)  # shop name or URL, as typed
    note: Mapped[str | None] = mapped_column(Text)
    #: "pending" | "approved" | "declined"
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL")
    )
    #: The invite an approval created (bound to ``email``).
    invite_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invite_code.id", ondelete="SET NULL"))


class AiCall(Base):
    """One call to the AI provider: who for, what for, which model, the tokens of
    each billing class, whether it worked, and what it cost (core/ai_meter.py).

    Admin-only: our cost of goods, never in a response a seller can receive.
    Counts and a purpose, never text. ``tenant_id`` is SET NULL when an account
    is deleted, because the spend remains ours. Kept ``RETENTION_DAYS``.
    """

    __tablename__ = "ai_call"
    __table_args__ = (Index("ix_ai_call_tenant_day", "tenant_id", "day"),)
    #: 25 months. The monthly cost view compares the last 12 months with the 12
    #: before them, which start up to 24 calendar months back; 25 calendar
    #: months are never more than 763 days.
    RETENTION_DAYS: ClassVar[int] = 765

    id: Mapped[uuid.UUID] = _uuid_pk()
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The UTC day of ``at``: how the provider's console groups, and what is summed.
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id", ondelete="SET NULL"))
    #: vision | content | content_retry | size_chart | eval | backfill | other
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    #: False: refused, unusable answer, or the request itself failed (no tokens then).
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    #: Prompt tokens that were not cached.
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Written to the 5-minute cache, and to the 1-hour cache (priced differently).
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cache_write_1h_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: At the price table in force when recorded; None while the model has no price.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    #: 1 on the call that wrote a listing the seller received, else 0.
    listings: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer)


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
    #: How many listings the shop has in each state, as Etsy reported at the last
    #: sync ({"active": 2940, "draft": 37, ...}), and whether every page was read.
    listing_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    listing_counts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ConnectionStatus] = mapped_column(
        _enum(ConnectionStatus, "connection_status"),
        nullable=False,
        default=ConnectionStatus.active,
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: The seller's shop group (v8 §B/§C), at most one; None = in no group.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shop_group.id", ondelete="SET NULL"), index=True
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
    #: The same requests by what they were for, ``{"drafts": n, ...}``
    #: (etsy/categories.py); NULL on days recorded before it was kept.
    categories: Mapped[dict[str, int] | None] = mapped_column(JSONB_TYPE)


class DraftAttempt(Base):
    """A draft being created in one shop for one content: what makes creating it
    resumable.

    Creating a draft is some fifteen Etsy requests. The publication row is only
    written when all of them have succeeded, so without this a failure after
    ``createDraftListing`` (a timeout on an image, a 429 that outlasted its
    retries, a worker restart) left a draft on Etsy the app did not know about,
    and trying again made a second one. The row is written before the create
    request and holds the listing id as soon as Etsy returns it; the next try
    carries on with that listing. It is deleted when the draft is finished.

    Holds the app's own bookkeeping (an Etsy listing id and the title that was
    sent), not Etsy content. Removed with the shop, and after
    :attr:`RETENTION_DAYS` if never finished.
    """

    __tablename__ = "draft_attempt"
    __table_args__ = (UniqueConstraint("content_id", "connection_id", name="uq_draft_attempt_content_shop"),)

    RETENTION_DAYS: ClassVar[int] = 30

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_content.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Set once Etsy has returned it (or the draft was found again after a lost answer).
    etsy_listing_id: Mapped[int | None] = mapped_column(BigInteger)
    #: When createDraftListing was last sent, and the title it carried: how a
    #: draft whose answer never arrived is recognised in the shop's drafts.
    create_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
    #: The name the seller gave it, to find it again; NULL = the name derived
    #: from its contents (pipeline/batch_names.py).
    #: How the seller chose to turn the photos into listings: "folder" | "sku" |
    #: "one" (pipeline/grouping.py::MODES); None: folders, then SKUs (the default).
    grouping_mode: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    #: The shop this batch is for: the seller's explicit choice, shown first on the
    #: batch page. Groups start from it and may each choose another (Priority 2).
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="SET NULL")
    )
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
    #: The shop this group's listing is written for. Its profile and its size-chart
    #: profile always belong to this shop.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="SET NULL")
    )
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
    #: Where the size charts sit among this group's photos, dragged by the seller:
    #: one slot per chart (photos before it, -1 = after every photo); None follows
    #: the profile's ``size_chart_position`` (pipeline/chart_order.py).
    chart_slots: Mapped[list[int] | None] = mapped_column(JSONB_TYPE)


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
    #: The folder the file was uploaded in ("" or None: loose), kept so the batch
    #: can be grouped again another way before anything is written (pipeline/grouping.py).
    upload_folder: Mapped[str | None] = mapped_column(Text)
    #: The original upload's key. None: not kept (every upload since migration
    #: 0053, and older ones once ``storage-originals --apply`` has run): nothing
    #: reads an original once its processed copy exists.
    storage_key: Mapped[str | None] = mapped_column(Text)
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
    #: When upload retention deleted this image's files: the original, the
    #: processed copy and their previews (pipeline/upload_retention.py). The row
    #: stays, so the batch, the listing text and its publication still read as
    #: they did; ``processed_key`` is cleared, so nothing tries to use the file.
    files_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: A small JPEG of the listing's cover, kept after the files are deleted so
    #: the app still shows what the listing is. Only the group's cover has one.
    thumbnail_key: Mapped[str | None] = mapped_column(Text)


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
    #: The cover the title and tags were written from. ``asset_id`` follows the
    #: group's cover; when they differ, the seller is told the text came from the
    #: old cover and offered "Regenerate". Not a foreign key: the photo may be gone.
    written_from_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid())
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
    #: This listing's own personalization (v8 §D), the setting sent to Etsy in
    #: every shop it goes to: None follows its profile's; {"enabled": false} is off;
    #: otherwise the question (pipeline/personalization.py).
    personalization: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ListingPublication(Base):
    """One Etsy draft made from one piece of generated content, in one shop.

    Publishing the same content to N shops makes N drafts (docs/duzeltmeler-v5.md
    §E), each with its own row. Deleted only when that shop is disconnected
    (CLAUDE.md: Etsy content goes with the shop); the generated content itself
    is the seller's work and stays.

    **Never hard-deleted while its shop is connected.** A draft the seller
    deleted on Etsy is marked (``state`` "deleted_on_etsy",
    ``deleted_on_etsy_at``) and detached from its content, so the same listing
    can be drafted again while counts and Analytics keep the record
    (:func:`app.etsy.publisher.mark_deleted_on_etsy`).

    **This row is the record that a listing was created and published, and it
    outlives the batch.** Deleting a batch removes the uploads and the working
    content; the publication is detached from the content (``content_id``
    becomes NULL), not deleted. It used to cascade, so a seller tidying up
    finished batches erased every trace that anything had been published.
    What the record needs once the content is gone is kept on the row itself:
    the title and SKU the draft was made with, and when it went live.
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
    #: The content the draft was made from; NULL once its batch has been deleted.
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_content.id", ondelete="SET NULL")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The seller's own title and SKU as sent to Etsy (not read back from Etsy),
    #: so the record still says what it was after the content is gone.
    title: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(Text)
    #: When the seller published it (it went from draft to active through the app).
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The profile of *that* shop the draft was built from.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="SET NULL")
    )
    etsy_listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: "draft" on create; "active" once the seller explicitly publishes it;
    #: "deleted_on_etsy" once Etsy says the listing no longer exists.
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    #: When the app found the listing gone on Etsy (state "deleted_on_etsy").
    deleted_on_etsy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    #: The profile's **main shop** (v8 §C): its reference listing is there, and the
    #: shared settings (category, prices, variations, size charts, description...)
    #: are read from it. The profile is the account's: other shops use it through
    #: a :class:`ProfileShopLink` holding their own ids. When the main shop is
    #: disconnected and another linked shop remains, that shop becomes the main one.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: The reference listing in the main shop; None after the main shop changed
    #: and no reference has been chosen in the new one yet.
    reference_listing_id: Mapped[int | None] = mapped_column(BigInteger)
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
    #: How listings are written with this profile: "classic" ("Long keyword":
    #: 110-140 character keyword titles) or "search" ("Etsy recommended (short)":
    #: at most 15 words, tags that do not repeat it, a design-specific description
    #: opening and attributes; pipeline/search_rules.py). The seller chooses, per
    #: profile. New profiles start on "search"; migration 0050 left existing ones
    #: on "classic".
    listing_style: Mapped[str] = mapped_column(Text, nullable=False, server_default="search")
    #: The "search" style's title length bounds; None = the default (40-140).
    title_min_length: Mapped[int | None] = mapped_column(Integer)
    title_max_length: Mapped[int | None] = mapped_column(Integer)
    #: Where its size charts go on a draft: "after_cover" (2nd), "third" or "last"
    #: (the default, what drafts always did). A group can drag them elsewhere.
    size_chart_position: Mapped[str] = mapped_column(Text, nullable=False, server_default="last")
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


class ShopGroup(Base):
    """A named group of the account's shops (v8 §B): every shop of a group gets
    the same listings. A shop is in at most one group (``etsy_connection.group_id``)."""

    __tablename__ = "shop_group"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_shop_group_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProfileShopLink(Base):
    """A profile used in one shop (v8 §C): that shop's own ids.

    The main shop's link (``profile.connection_id``) holds no ids: its ids are its
    reference listing's own. Any other shop's link holds the shipping profile,
    return policy, processing profile and production partners of *that* shop,
    found by exact name or identical terms, created there on the seller's
    confirmation, or picked by the seller. They are the seller's own settings'
    ids, not listing content; deleted with the shop's link when it is
    disconnected. ``notes`` says, per setting, why it is not linked yet.
    """

    __tablename__ = "profile_shop_link"
    __table_args__ = (UniqueConstraint("profile_id", "connection_id", name="uq_profile_shop_link"),)

    #: Checked again in the background this often while the profile is in use.
    CHECK_SECONDS: ClassVar[int] = 20 * 3600

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shipping_profile_id: Mapped[int | None] = mapped_column(BigInteger)
    return_policy_id: Mapped[int | None] = mapped_column(BigInteger)
    readiness_state_id: Mapped[int | None] = mapped_column(BigInteger)
    production_partner_ids: Mapped[list[int] | None] = mapped_column(JSONB_TYPE)
    #: "checking" (being linked), "ready", "incomplete" (see notes), "error".
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="checking")
    #: {resource: why it is not linked}, the seller's words.
    notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    #: Creations the seller confirmed, waiting for the job: [resource, ...].
    pending_create: Mapped[list[str] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DesignDistribution(Base):
    """Which group a design was sent to (v8 §B), remembered so sending it to
    another group later can be warned about (never blocked). The seller's own
    record, not Etsy content; outlives the batch like publication history:
    ``content_id`` and ``group_id`` become NULL, the SKU and group name stay."""

    __tablename__ = "design_distribution"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_content.id", ondelete="SET NULL"), index=True
    )
    #: The design's SKU, so the same design uploaded again in another batch is known.
    sku: Mapped[str | None] = mapped_column(Text, index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shop_group.id", ondelete="SET NULL"))
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GroupPlan(Base):
    """A confirmed group schedule (v8 §B): the seller's settings, and its slots."""

    __tablename__ = "group_plan"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("upload_batch.id", ondelete="SET NULL"))
    #: start date, listings per shop per day, window, spacing, stagger, time zone.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlannedSlot(Base):
    """One listing in one shop at one time, from a confirmed group schedule.

    The draft is created at ``draft_at`` (a cron releases it; a draft job is
    gated like any other and spills to the next day when the budget is full),
    and once it exists its publication is scheduled for ``publish_at``, the time
    the seller confirmed. Going live re-checks the approval and the compliance
    findings (workers/schedule.py), exactly as a schedule set by hand does.
    """

    __tablename__ = "planned_slot"
    __table_args__ = (Index("ix_planned_slot_due", "state", "draft_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("group_plan.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_content.id", ondelete="SET NULL"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: "waiting" (draft not started) | "drafting" | "scheduled" (draft made, go-live set)
    #: | "cancelled" | "failed" (with ``note``).
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="waiting")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)


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


class ListingStatDaily(Base):
    """One app-published listing's views and favourites on Etsy, one row per UTC day (Part D).

    Etsy gives each listing's **lifetime** ``views`` and ``num_favorers``; the
    daily figure is the difference from the day before. ``views``/``favorites``
    are None when there is no earlier total to subtract (the first reading of a
    listing published more than :data:`app.workers.listing_stats.FIRST_DAY_WINDOW`
    ago), never a made-up number. These are listing page views on Etsy, not
    search impressions: Etsy's API has neither impressions, search terms nor
    traffic sources (docs/analytics.md). Only for listings the app published;
    kept :attr:`RETENTION_DAYS` (13 months), deleted at once with the shop.
    """

    __tablename__ = "listing_stat_daily"
    __table_args__ = (Index("ix_listing_stat_daily_tenant_day", "tenant_id", "day"),)

    RETENTION_DAYS: ClassVar[int] = 396

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: Etsy's lifetime totals as read that day (None: Etsy returned none).
    views_total: Mapped[int | None] = mapped_column(Integer)
    favorites_total: Mapped[int | None] = mapped_column(Integer)
    #: That day's increase; None = unknown (no earlier total).
    views: Mapped[int | None] = mapped_column(Integer)
    favorites: Mapped[int | None] = mapped_column(Integer)


class ContentVersion(Base):
    """One version of a listing's text as it went to Etsy in one shop (Part D).

    Written when the draft is made (``active_from`` None while it is a draft),
    opened when it goes live, closed (``active_to``) when the app replaces the
    text. ``reason``: "generated" (as the app wrote it), "edited" (the seller
    changed it in the app first) or "replaced" (Replace images, full mode).
    Edits made directly on Etsy are not seen. ``title_style`` is "short" or
    "long". Survives batch deletion (it hangs off the publication); kept 13
    months after it was closed and deleted with the shop.
    """

    __tablename__ = "content_version"
    __table_args__ = (Index("ix_content_version_publication", "publication_id", "active_from"),)

    RETENTION_DAYS: ClassVar[int] = 396

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False
    )
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing_publication.id", ondelete="CASCADE"), nullable=False
    )
    etsy_listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(JSONB_TYPE)
    description: Mapped[str | None] = mapped_column(Text)
    #: The attributes written on the draft, ``{name: value}``.
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB_TYPE)
    title_style: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalesSync(Base):
    """Where reading one shop's sales stands (v7 §C1): resumable, paced, measured.

    The first read of a shop covers 13 months and can take many requests, so it
    runs in chunks, adds each page to ``sales_daily`` as it goes, and stops for
    the day at :attr:`DAILY_REQUESTS`. ``next_offset`` and the cursors make it
    resume exactly: a sale already counted is skipped by its (created time,
    transaction id) even when new sales shift the pages during the read. After
    that, reads take only sales newer than ``newest_*``. Holds counts and those
    two cursors only: nothing about buyers. Deleted with the shop.
    """

    __tablename__ = "sales_sync"

    #: Requests one shop's sales reading may use per UTC day.
    DAILY_REQUESTS: ClassVar[int] = 250

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed"
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="estimating")
    #: "desc" (newest first, what Etsy returns) or "asc"; learned from the data.
    direction: Mapped[str | None] = mapped_column(Text)
    #: All of the shop's sales Etsy reports, and how many fall in the 13 months read.
    total_count: Mapped[int | None] = mapped_column(Integer)
    window_count: Mapped[int | None] = mapped_column(Integer)
    #: Requests the first read needs (estimate), and where the read is.
    pages_estimate: Mapped[int | None] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_offset: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    window_start: Mapped[date | None] = mapped_column(Date)
    #: Cursors: the oldest and newest sale counted, as (created time, transaction id).
    oldest_ts: Mapped[int | None] = mapped_column(BigInteger)
    oldest_id: Mapped[int | None] = mapped_column(BigInteger)
    newest_ts: Mapped[int | None] = mapped_column(BigInteger)
    newest_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Requests used: by this read (estimate included), and today (the daily pace).
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requests_day: Mapped[date | None] = mapped_column(Date)
    requests_today: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The last incremental read's request count (usually 1).
    last_update_requests: Mapped[int | None] = mapped_column(Integer)
    resumes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    #: Held by the run reading this shop now, so two runs never read it at once.
    lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The read wrote :class:`SaleLine` rows for everything it counted. False for
    #: a shop read before those existed: the nightly round reads it once more.
    has_lines: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    #: The one-time second read that adds the order lines: None (never begun; due
    #: when the shop is ``complete`` without lines), "reading" (begun, including
    #: while it waits for the next night) or "done". Shops in it share a nightly
    #: slice of the app's budget and are read one at a time (workers/sales.py).
    reread: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LedgerDaily(Base):
    """The shop's payment account ledger, totalled per day and entry type (v7 §C).

    Derived from getShopPaymentAccountLedgerEntries: only each entry's type,
    amount, currency and date are read; the entries themselves are not kept.
    Gives the shop's real Etsy fees and ad spend (``prolist``,
    ``offsite_ads_fee``) per day. Kept 13 months like sales; deleted with the shop.
    """

    __tablename__ = "ledger_daily"
    __table_args__ = (Index("ix_ledger_daily_tenant_day", "tenant_id", "day"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    #: Etsy's ledger_type, as Etsy names it ("prolist", "offsite_ads_fee", ...).
    ledger_type: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: Sum of the entries' amounts in minor units, signed as Etsy signs them
    #: (a fee is negative, a payment positive).
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    entries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str | None] = mapped_column(Text)


class LedgerSync(Base):
    """Where reading one shop's ledger stands: estimated, paced, resumable.

    The first read covers :attr:`FIRST_DAYS` (entries for a fixed window, read
    100 at a time by offset, so a resumed read continues exactly); after that
    only entries created since ``synced_until``. Then the history is filled in
    backwards, a slice of :attr:`SLICE_DAYS` at a time, to the 13-month edge
    (``backfill_*``, ``covered_from``, ``slice_*``): low priority, inside the
    same daily cap. Holds counts and times only.
    """

    __tablename__ = "ledger_sync"

    FIRST_DAYS: ClassVar[int] = 90
    DAILY_REQUESTS: ClassVar[int] = 250
    #: The backfill reads this many days per slice, oldest slice last.
    SLICE_DAYS: ClassVar[int] = 30
    #: Requests a day the backfill leaves for the nightly update.
    BACKFILL_RESERVE: ClassVar[int] = 30

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    #: "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed"
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="estimating")
    window_start: Mapped[int | None] = mapped_column(BigInteger)  # epoch seconds
    window_end: Mapped[int | None] = mapped_column(BigInteger)
    total_count: Mapped[int | None] = mapped_column(Integer)
    next_offset: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Everything created up to this time (epoch seconds) has been read.
    synced_until: Mapped[int | None] = mapped_column(BigInteger)
    #: End of the update window being read (with next_offset); None between updates.
    update_end: Mapped[int | None] = mapped_column(BigInteger)
    #: "none" | "reading" | "waiting" | "complete" | "failed"
    backfill_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    #: Epoch second (a UTC midnight) the backfill reads back to: the 13-month edge when it began.
    backfill_target: Mapped[int | None] = mapped_column(BigInteger)
    #: Everything from this epoch second on is read; None means window_start.
    covered_from: Mapped[int | None] = mapped_column(BigInteger)
    #: The slice being read is [slice_start, covered_from - 1], at slice_offset.
    slice_start: Mapped[int | None] = mapped_column(BigInteger)
    slice_offset: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    backfill_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    backfill_note: Mapped[str | None] = mapped_column(Text)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requests_day: Mapped[date | None] = mapped_column(Date)
    requests_today: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    resumes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SaleLine(Base):
    """One line of one order, as attribution needs it (Analytics, pipeline/attribution.py).

    From getShopReceiptTransactionsByShop, the same response the daily totals
    are made from: the order (``receipt_id``), the listing, the quantity, the
    line's price, the shipping the buyer paid for it and the date. **Nothing
    about the buyer**: no name, address, message or buyer id is read or stored.
    It lets an order row of an imported statement be tied to its listings, and a
    multi-item order's fees be split by each item's share of the price.

    Kept as long as the daily totals (13 months); deleted with the shop.
    """

    __tablename__ = "sale_line"
    __table_args__ = (
        Index("ix_sale_line_receipt", "connection_id", "receipt_id"),
        Index("ix_sale_line_day", "connection_id", "day"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    transaction_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    receipt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)  # UTC, like sales_daily
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The line's price (unit price x quantity) and the shipping paid for it, in minor units.
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    shipping_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    currency: Mapped[str | None] = mapped_column(Text)


class StatementImport(Base):
    """One month of one shop's Etsy statement, as totals (pipeline/statement.py).

    The seller downloads the CSV from Shop Manager and uploads it; it is parsed
    in memory and **the file is never kept**. What is kept: a total per
    category (they add up to ``net_minor`` exactly), the deposits, and what the
    reader could not classify. It is the authority for the month's money:
    imported statement > ledger API > nothing.

    Kept 13 months like the sales it explains; deleted with the shop. Importing
    a month again replaces it.
    """

    __tablename__ = "statement_import"

    RETENTION_DAYS: ClassVar[int] = 396

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)  # the 1st
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    currency: Mapped[str | None] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    first_day: Mapped[date] = mapped_column(Date, nullable=False)
    last_day: Mapped[date] = mapped_column(Date, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: {category: minor units} and {category: rows}, as pipeline/statement.py names them.
    totals: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    #: Fee credits by what they credit ({"listing": minor, "processing": ...}).
    credits: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)
    #: Transfers to the bank: [{"day": "2026-09-07", "minor": 4000}]. Not income, not cost.
    deposits: Mapped[list[Any]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    #: Rows with no rule ([{"type", "title", "category", "minor"}]) and reader's notes.
    unrecognised: Mapped[list[Any]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    notes: Mapped[list[Any]] = mapped_column(JSONB_TYPE, nullable=False, default=list)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StatementOrder(Base):
    """What a statement says about one order: amounts per category, by receipt id.

    The per-order rows attribution needs, and nothing else: the order's number,
    the day its sale was posted and amounts. The statement carries no buyer
    data and none is added. Lives and dies with its :class:`StatementImport`.
    """

    __tablename__ = "statement_order"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    #: The day the sale was posted; NULL when only fees or a refund fall in this month.
    day: Mapped[date | None] = mapped_column(Date)
    #: {category: minor units}
    amounts: Mapped[dict[str, Any]] = mapped_column(JSONB_TYPE, nullable=False, default=dict)


class StatementListingFee(Base):
    """Listing fees a statement ties to a listing id (``Listing #<id>`` rows)."""

    __tablename__ = "statement_listing_fee"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    fees: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")  # negative
    credits_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class AdCharge(Base):
    """What a statement billed for one day's Etsy Ads clicks ("Charged by Etsy").

    The charge belongs to its click day and is posted the day after, so a
    month's statement holds the last day of the month before and not its own
    last day. Shop level: Etsy does not say which listing.
    """

    __tablename__ = "ad_charge"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    click_day: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    posted: Mapped[date] = mapped_column(Date, nullable=False)
    #: The statement month the charge is on.
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)  # a cost: positive


class AdsDaily(Base):
    """The Etsy Ads report, one row per day **for the whole shop** ("Ad spend for clicks").

    From the report the seller downloads in Shop Manager > Marketing > Etsy Ads.
    It has no listing column, so ad spend is never tied to a listing, not even
    proportionally. Days are Eastern Time, as Etsy reports them. Kept 13
    months; deleted with the shop. Importing a range again replaces its days.
    """

    __tablename__ = "ads_daily"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False)
    views: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    orders: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    currency: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
