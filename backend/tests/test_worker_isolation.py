"""Queue isolation (production-spec B5).

A job carries the tenant it was queued for. Rows it loads by id must belong to
that tenant, so a payload that points at another tenant's content fails the job
instead of acting on it.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models import Asset, GeneratedContent, Tenant
from app.workers.guards import CrossTenantJob, owned, owned_optional


class _Row:
    def __init__(self, tenant_id: uuid.UUID | None) -> None:
        self.tenant_id = tenant_id


def test_owned_accepts_the_jobs_own_tenant() -> None:
    tenant = uuid.uuid4()
    row = _Row(tenant)
    assert owned(row, tenant, "content") is row


def test_owned_rejects_another_tenants_row() -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(CrossTenantJob):
        owned(_Row(theirs), mine, "content")


def test_owned_rejects_a_missing_row() -> None:
    with pytest.raises(CrossTenantJob):
        owned(None, uuid.uuid4(), "content")


def test_owned_rejects_a_row_with_no_tenant() -> None:
    """A row without tenant_id cannot be proven to belong to anyone."""
    with pytest.raises(CrossTenantJob):
        owned(_Row(None), uuid.uuid4(), "content")


def test_error_message_does_not_confirm_existence() -> None:
    """job.last_error is readable by the owning tenant, so it must stay vague."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(CrossTenantJob) as missing:
        owned(None, mine, "content")
    with pytest.raises(CrossTenantJob) as foreign:
        owned(_Row(theirs), mine, "content")
    assert str(missing.value) == str(foreign.value) == "content not found"
    assert str(theirs) not in str(foreign.value)


def test_owned_optional_allows_absent_but_not_foreign() -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    assert owned_optional(None, mine) is None
    row = _Row(mine)
    assert owned_optional(row, mine) is row
    with pytest.raises(CrossTenantJob):
        owned_optional(_Row(theirs), mine)


def test_guard_works_on_the_real_models() -> None:
    """The guard reads tenant_id off ORM instances, not just the stub above."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()

    content = GeneratedContent(tenant_id=theirs, batch_id=uuid.uuid4(), asset_id=uuid.uuid4())
    with pytest.raises(CrossTenantJob):
        owned(content, mine, "content")

    asset = Asset(tenant_id=mine, batch_id=uuid.uuid4(), original_filename="x.png")
    assert owned(asset, mine, "asset") is asset

    # Tenant itself has no tenant_id column, so it can never satisfy the guard —
    # workers load it by job.tenant_id directly rather than through owned().
    with pytest.raises(CrossTenantJob):
        owned(Tenant(email="x@example.com", password_hash="!"), mine, "tenant")
