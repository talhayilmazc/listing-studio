"""Log redaction and tenant-facing job errors (production-spec D)."""

from __future__ import annotations

import logging

import pytest

from app.core.logsafety import install_log_redaction, redact
from app.etsy.errors import EtsyClientError
from app.workers.guards import CrossTenantJob, public_error


@pytest.fixture(autouse=True)
def _redaction_installed() -> None:
    install_log_redaction()


def test_oauth_callback_credentials_are_scrubbed() -> None:
    line = "GET /api/auth/etsy/callback?state=Zm9vYmFy&code=abc123-secret HTTP/1.1"
    out = redact(line)
    assert "Zm9vYmFy" not in out and "abc123-secret" not in out
    assert "/api/auth/etsy/callback?" in out  # the route itself stays readable


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi.payload.sig"),
        ("cookie: session=Q1w2E3r4T5y6U7i8", "Q1w2E3r4T5y6U7i8"),
        ('{"email": "a@b.co", "password": "hunter2hunter2"}', "hunter2hunter2"),
        ("x-api-key: keystring:sharedsecret", "keystring:sharedsecret"),
        ("refresh_token=rt_live_987&x=1", "rt_live_987"),
        ("contact talha@example.com for access", "talha@example.com"),
    ],
)
def test_secrets_and_addresses_are_scrubbed(text, secret) -> None:
    assert secret not in redact(text)


def test_every_logger_is_covered(caplog) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("some.library").info("user %s logged in with password=%s", "a@b.co", "pw12")
    record = caplog.records[-1]
    assert "a@b.co" not in record.getMessage()
    assert "pw12" not in record.getMessage()


def test_uvicorn_access_record_keeps_its_shape(caplog) -> None:
    """uvicorn's formatter unpacks five args; only the path may change."""
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.5:4411",
        "GET",
        "/api/auth/etsy/callback?state=s3cr3t&code=c0d3",
        "1.1",
        302,
    )
    record = caplog.records[-1]
    assert isinstance(record.args, tuple) and len(record.args) == 5
    client, method, path, version, status = record.args
    assert (client, method, version, status) == ("203.0.113.5:4411", "GET", "1.1", 302)
    assert "s3cr3t" not in path and "c0d3" not in path


def test_tracebacks_are_scrubbed(caplog) -> None:
    caplog.set_level(logging.ERROR)
    try:
        raise ValueError("lookup failed for owner@example.com with token=abc.def")
    except ValueError:
        logging.getLogger("app.test").exception("job failed")
    record = caplog.records[-1]
    assert record.exc_text
    assert "owner@example.com" not in record.exc_text
    assert "abc.def" not in record.exc_text
    assert "ValueError" in record.exc_text  # the useful part survives


# --- job.last_error, which the owning tenant reads --------------------------
def test_authored_errors_reach_the_tenant() -> None:
    assert public_error(ValueError("publish job has no reference profile payload")) == (
        "publish job has no reference profile payload"
    )
    assert "400" in public_error(EtsyClientError(400, body="bad field"))
    assert public_error(CrossTenantJob("content not found")) == "content not found"


@pytest.mark.parametrize(
    "exc",
    [
        OSError(2, "No such file", "/data/storage/5e0c/processed/x.jpg"),
        RuntimeError("asyncpg connection to postgres:5432 refused"),
        KeyError("processed_key"),
    ],
)
def test_unexpected_errors_are_replaced(exc) -> None:
    message = public_error(exc)
    assert message == "the job failed unexpectedly; it has been logged for investigation"
    assert "/data/storage" not in message and "postgres" not in message


def test_authored_errors_are_still_scrubbed() -> None:
    assert "x@y.co" not in public_error(ValueError("could not notify x@y.co"))
