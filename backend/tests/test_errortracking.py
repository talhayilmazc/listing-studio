"""Optional Sentry reporting: off by default, scrubbed when on (production-spec F6)."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import errortracking
from app.core.config import Settings, set_settings_override
from app.core.errortracking import init_error_tracking, scrub_breadcrumb, scrub_event


@pytest.fixture(autouse=True)
def _reset_sentry():
    yield
    import sentry_sdk

    sentry_sdk.init()  # a client with no DSN: sends nothing
    errortracking._enabled = False


def test_off_without_a_dsn() -> None:
    assert init_error_tracking("api") is False
    assert errortracking.is_enabled() is False


def test_scrub_event_strips_identity_and_credentials() -> None:
    event = {
        "user": {"email": "seller@example.com", "ip_address": "203.0.113.5"},
        "request": {
            "url": "https://listyro.com/api/auth/etsy/callback?state=s3cret&code=c0de",
            "cookies": {"session": "tok"},
            "data": {"password": "hunter2hunter2"},
            "headers": {
                "Cookie": "session=tok",
                "Authorization": "Bearer abc.def",
                "X-Admin-Token": "admin",
                "CF-Connecting-IP": "203.0.113.5",
                "User-Agent": "Mozilla/5.0",
            },
        },
        "exception": {
            "values": [{"type": "ValueError", "value": "no shop for seller@example.com token=xyz"}]
        },
    }
    out = json.dumps(scrub_event(event))
    for leaked in ("seller@example.com", "203.0.113.5", "s3cret", "c0de", "hunter2hunter2",
                   "abc.def", "\"tok\"", "admin\""):
        assert leaked not in out, leaked
    assert "Mozilla/5.0" in out  # harmless context survives
    assert "ValueError" in out


def test_breadcrumbs_are_scrubbed() -> None:
    crumb = {"message": "GET /api/auth/etsy/callback?code=abc123", "data": {"to": "x@y.co"}}
    out = json.dumps(scrub_breadcrumb(crumb))
    assert "abc123" not in out and "x@y.co" not in out


async def test_a_real_crash_reaches_sentry_scrubbed(test_settings: Settings) -> None:
    """End to end: our 500 handler reports the crash, and the report is clean."""
    from sentry_sdk.transport import Transport

    sent: list = []

    class Capture(Transport):
        def capture_envelope(self, envelope) -> None:  # noqa: ANN001
            sent.append(envelope)

    set_settings_override(test_settings.model_copy(update={"sentry_dsn": "https://pub@o0.ingest.example/1"}))
    # Capture first: create_app() would otherwise start a real client, and the
    # second initialisation is (correctly) a no-op.
    assert init_error_tracking("api", transport=Capture(options=None)) is True
    from app.main import create_app

    app = create_app()

    @app.get("/api/_boom")
    async def _boom() -> None:
        raise RuntimeError("storage at /data/storage/x password=hunter2 owner@example.com")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/_boom", headers={"cookie": "session=secret-token"})
    assert resp.status_code == 500

    import sentry_sdk

    sentry_sdk.flush(timeout=2)
    events = [item.payload.json for env in sent for item in env.items if item.type == "event"]
    assert events, "the crash was not reported"
    blob = json.dumps(events)
    assert "RuntimeError" in blob
    for leaked in ("hunter2", "owner@example.com", "secret-token"):
        assert leaked not in blob, leaked
    # No local variables: stack frames carry no 'vars'.
    frames = [f for e in events for v in e["exception"]["values"]
              for f in (v.get("stacktrace") or {}).get("frames", [])]
    assert frames and all("vars" not in f for f in frames)


async def test_meta_says_whether_error_tracking_is_on(test_settings: Settings, monkeypatch) -> None:
    import app.main as main

    # Only the reporting flag is under test; never start a real client here.
    monkeypatch.setattr(main, "init_error_tracking", lambda *_a, **_k: False)
    create_app = main.create_app

    for dsn, expected in (("", False), ("https://pub@o0.ingest.example/1", True)):
        set_settings_override(test_settings.model_copy(update={"sentry_dsn": dsn}))
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
            assert (await c.get("/api/meta")).json()["error_tracking"] is expected
