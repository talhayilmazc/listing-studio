"""HTTP hardening at the edge of the app (production-spec D).

One pure-ASGI middleware, outermost, so it sees every request and every response
— including 404s, CORS preflights and crashes:

* **CSRF** — a state-changing request is refused unless its ``Origin`` (or, when
  a browser omits that, its ``Referer``) is one of ours. A request carrying the
  session cookie with *neither* header is refused too: it holds ambient
  credentials and nothing proves where it came from. A request with no cookie
  and no origin cannot be a CSRF — there is nothing to ride on — so CLI and
  server-to-server calls (the admin endpoints) still work.
* **Body ceiling** — ``Content-Length`` is checked before the app runs, and the
  stream is counted as it arrives, so an oversized or chunked body is cut off
  before it reaches the multipart parser and its temp files.
* **Security headers** on every response.
* **Crashes** — any unhandled exception is logged in full under a request id,
  and the caller gets a generic 500 carrying only that id.

Pure ASGI rather than ``BaseHTTPMiddleware``, which buffers and mishandles
streamed bodies — exactly what the upload path needs counted.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errortracking import capture
from app.core.sessions import SESSION_COOKIE

logger = logging.getLogger("app.security")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# JSON bodies here are small; only the upload endpoint takes files.
DEFAULT_BODY_LIMIT = 1024 * 1024
_UPLOAD_PATH = re.compile(r"^/api/batches/[^/]+/assets$")
_ARCHIVE_PATH = re.compile(r"^/api/batches/[^/]+/archive$")
_MULTIPART_ENVELOPE = 1024 * 1024  # boundaries, part headers, the group_key field

# Always set, overriding anything a handler chose.
SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # The API serves JSON and images, never documents, so it needs no sources.
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "Cross-Origin-Resource-Policy": "same-site",
}

# Set only when the handler did not: tenant data must not sit in a shared
# cache (Cloudflare included), but the resized previews opt into caching.
DEFAULT_HEADERS: dict[str, str] = {"Cache-Control": "no-store"}


class BodyTooLarge(HTTPException):
    """The request body outgrew its limit while streaming in.

    An HTTPException on purpose: FastAPI converts any *other* exception raised
    while it reads a body into a 400 "error parsing the body", which would hide
    the real reason. HTTPExceptions it re-raises untouched, so this surfaces as
    the 413 it is — whether FastAPI or this middleware ends up handling it.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")


def _normalise_origin(value: str) -> str | None:
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def allowed_origins() -> set[str]:
    """Our own origins: the configured CORS origins plus the frontend URL."""
    settings = get_settings()
    raw = [o for o in settings.cors_origins.split(",") if o.strip()] + [settings.frontend_url]
    return {n for n in (_normalise_origin(o) for o in raw) if n}


def csrf_verdict(method: str, headers: dict[str, str]) -> str | None:
    """Why this request must be refused, or ``None`` when it may proceed."""
    if method in SAFE_METHODS:
        return None
    allowed = allowed_origins()

    origin = headers.get("origin")
    if origin is not None:
        # "null" (sandboxed frames, some redirects) normalises to None: refused.
        return None if _normalise_origin(origin) in allowed else "origin not allowed"

    referer = headers.get("referer")
    if referer:
        return None if _normalise_origin(referer) in allowed else "referer not allowed"

    if _has_session_cookie(headers.get("cookie", "")):
        return "credentialed request without an origin"
    return None


def _has_session_cookie(cookie_header: str) -> bool:
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            return True
    return False


def body_limit(path: str) -> int:
    if _UPLOAD_PATH.match(path):
        return get_settings().max_upload_bytes + _MULTIPART_ENVELOPE
    if _ARCHIVE_PATH.match(path):
        return get_settings().max_archive_bytes + _MULTIPART_ENVELOPE
    return DEFAULT_BODY_LIMIT


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = secrets.token_hex(8)
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"])
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                for name, value in DEFAULT_HEADERS.items():
                    if name.lower() not in headers:
                        headers[name] = value
                headers["X-Request-ID"] = request_id
            await send(message)

        method = scope["method"]
        path = scope["path"]
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }

        verdict = csrf_verdict(method, headers)
        if verdict is not None:
            logger.warning("csrf: refused %s %s (%s) [%s]", method, path, verdict, request_id)
            await _json(send_with_headers, 403, {"detail": "cross-site request blocked"})
            return

        limit = body_limit(path)
        declared = headers.get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True  # a malformed length is not something to parse around
            if too_big:
                await _json(send_with_headers, 413, {"detail": "request body too large"})
                return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, counted_receive, send_with_headers)
        except BodyTooLarge:
            if not started:
                await _json(send_with_headers, 413, {"detail": "request body too large"})
        except Exception:
            # Full trace to the log (scrubbed by logsafety); nothing internal
            # to the caller beyond an id they can quote to support.
            logger.exception("unhandled error in %s %s [%s]", method, path, request_id)
            # This handler catches the exception before Sentry's ASGI hooks can,
            # so report it explicitly (a no-op when error tracking is off).
            capture(sys.exc_info()[1])
            if started:
                raise
            await _json(
                send_with_headers,
                500,
                {"detail": "internal error", "request_id": request_id},
            )


async def _json(send: Send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
