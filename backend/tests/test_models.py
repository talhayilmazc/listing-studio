"""Model creation and constraint tests for the step-1 data model."""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    ApiUsage,
    Asset,
    AssetStatus,
    ComplianceFinding,
    ComplianceSeverity,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingSnapshot,
    Tenant,
    TenantStatus,
    UploadBatch,
    UploadBatchStatus,
)


def _make_tenant(session: Session, email: str = "seller@example.com") -> Tenant:
    tenant = Tenant(email=email, password_hash="argon2$hash")
    session.add(tenant)
    session.commit()
    return tenant


def _make_connection(session: Session, tenant: Tenant) -> EtsyConnection:
    conn = EtsyConnection(
        tenant_id=tenant.id,
        shop_id=12345,
        access_token_enc=b"encrypted-access",
        refresh_token_enc=b"encrypted-refresh",
        status=ConnectionStatus.active,
    )
    session.add(conn)
    session.commit()
    return conn


def test_tenant_defaults(session: Session) -> None:
    tenant = _make_tenant(session)
    assert isinstance(tenant.id, uuid.UUID)
    assert tenant.status == TenantStatus.active
    assert tenant.daily_quota == 2000  # server default
    assert tenant.created_at is not None
    assert tenant.updated_at is not None


def test_email_unique(session: Session) -> None:
    _make_tenant(session, email="dup@example.com")
    session.add(Tenant(email="dup@example.com", password_hash="x"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_enum_roundtrip(session: Session) -> None:
    tenant = Tenant(email="s2@example.com", password_hash="x", status=TenantStatus.suspended)
    session.add(tenant)
    session.commit()
    session.expire_all()
    reloaded = session.get(Tenant, tenant.id)
    assert reloaded is not None
    assert reloaded.status == TenantStatus.suspended


def test_connection_repr_hides_tokens(session: Session) -> None:
    tenant = _make_tenant(session)
    conn = _make_connection(session, tenant)
    text = repr(conn)
    # Token material must never surface in repr / logs.
    assert "encrypted-access" not in text
    assert "encrypted-refresh" not in text
    assert "token" not in text.lower()
    # But useful identifiers are present.
    assert str(conn.shop_id) in text


def test_job_defaults(session: Session) -> None:
    tenant = _make_tenant(session)
    conn = _make_connection(session, tenant)
    job = Job(tenant_id=tenant.id, connection_id=conn.id, type=JobType.create_draft)
    session.add(job)
    session.commit()
    session.refresh(job)
    assert job.status == JobStatus.queued
    assert job.attempts == 0
    assert job.max_attempts == 5
    assert job.payload == {}


def test_api_usage_composite_pk(session: Session) -> None:
    tenant = _make_tenant(session)
    today = date(2026, 8, 15)
    session.add(ApiUsage(tenant_id=tenant.id, usage_date=today, request_count=1))
    session.commit()
    session.add(ApiUsage(tenant_id=tenant.id, usage_date=today, request_count=2))
    with pytest.raises(IntegrityError):
        session.commit()


def test_foreign_key_enforced(session: Session) -> None:
    # tenant_id points to a non-existent tenant -> FK violation.
    orphan = EtsyConnection(tenant_id=uuid.uuid4(), status=ConnectionStatus.active)
    session.add(orphan)
    # Postgres raises IntegrityError; SQLite surfaces FK failures as OperationalError.
    with pytest.raises((IntegrityError, OperationalError)):
        session.commit()


def test_listing_snapshot_retention_constant() -> None:
    # Retention threshold comes from CLAUDE.md and supersedes docs/data-model.md.
    assert ListingSnapshot.RETENTION_DAYS == 90


def test_listing_snapshot_roundtrip(session: Session) -> None:
    tenant = _make_tenant(session)
    conn = _make_connection(session, tenant)
    job = Job(tenant_id=tenant.id, connection_id=conn.id, type=JobType.update_listing)
    session.add(job)
    session.commit()

    snap = ListingSnapshot(
        tenant_id=tenant.id,
        listing_id=987654321,
        job_id=job.id,
        payload={"title": "before", "tags": ["a", "b"]},
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    assert snap.taken_at is not None
    assert snap.payload["title"] == "before"


def test_generated_content_and_compliance(session: Session) -> None:
    tenant = _make_tenant(session)
    batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=1)
    session.add(batch)
    session.commit()

    asset = Asset(
        batch_id=batch.id,
        tenant_id=tenant.id,
        original_filename="SKU123_front.png",
        parsed_sku="SKU123",
        storage_key="r2/asset/1",
        status=AssetStatus.uploaded,
    )
    session.add(asset)
    session.commit()

    content = GeneratedContent(
        tenant_id=tenant.id,
        batch_id=batch.id,
        asset_id=asset.id,
        title="A nice title",
        tags=["one", "two", "three"],
        input_tokens=100,
        output_tokens=50,
    )
    session.add(content)
    session.commit()
    session.refresh(content)
    assert content.approved is False  # server default
    assert content.tags == ["one", "two", "three"]

    finding = ComplianceFinding(
        tenant_id=tenant.id,
        generated_content_id=content.id,
        severity=ComplianceSeverity.blocking,
        rule="trademark",
        detail="Contains a protected brand name.",
    )
    session.add(finding)
    session.commit()
    session.refresh(finding)
    assert finding.severity == ComplianceSeverity.blocking
