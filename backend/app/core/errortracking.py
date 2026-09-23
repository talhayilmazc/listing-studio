"""Optional error reporting to Sentry (production-spec F6).

Off unless ``SENTRY_DSN`` is set. When on, Sentry becomes a processor of error
data, so the Privacy Policy names it only in that case (``/api/meta`` reports
``error_tracking``) — the policy stays true whichever way it is configured.

What leaves the server is deliberately narrow:

* no personal data by default (``send_default_pii=False``): no cookies, no user
  IP, no user identity;
* no request bodies, and no local variables — a stack frame's locals are
  exactly where tokens and passwords sit;
* every remaining string passes through the same scrubber as our logs, and
  credential-bearing headers are dropped outright;
* no performance tracing: errors only.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.core.logsafety import redact

logger = logging.getLogger(__name__)

# Request headers that carry credentials or identify the person. Dropped whole.
_DROP_HEADERS = frozenset(
    {"cookie", "authorization", "x-admin-token", "cf-connecting-ip", "x-forwarded-for", "x-real-ip"}
)

_enabled = False


def init_error_tracking(component: str, *, transport: Any = None) -> bool:
    """Start Sentry for this process (``api`` or ``worker``). Returns whether it is on.

    ``transport`` exists for tests, to capture what would be sent.
    """
    global _enabled
    dsn = get_settings().sentry_dsn.strip()
    if not dsn or _enabled:
        return _enabled

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=get_settings().app_env,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        **({"transport": transport} if transport is not None else {}),
    )
    sentry_sdk.set_tag("component", component)
    _enabled = True
    logger.info("error tracking enabled for %s", component)
    return True


def is_enabled() -> bool:
    return bool(get_settings().sentry_dsn.strip())


def capture(exc: BaseException) -> None:
    """Report an exception we handled ourselves (no-op when tracking is off)."""
    if not _enabled:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(exc)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(v) for v in value)
    return value


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Final gate before an event leaves the server."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in _DROP_HEADERS
            }
    event.pop("user", None)
    return _scrub(event)


def scrub_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    return _scrub(crumb)
