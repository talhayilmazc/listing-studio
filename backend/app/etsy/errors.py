"""Etsy API error taxonomy and HTTP-status classification.

These drive the worker retry policy:
- 429              -> retryable, honour ``Retry-After`` if present
- 5xx              -> retryable with exponential backoff
- 4xx (except 429) -> permanent failure, no retry

The exception ``str()`` stays generic (status code only), so nothing unexpected
reaches a log line or an error tracker through it. Etsy's response (``body``)
and the request ``path``/``method`` are carried as attributes; they never
contain a token (tokens ride request headers, not the response body or URL).

What the seller is shown is built from those by :func:`explain`: which step
Etsy refused and Etsy's own one-line reason (``detail``). That reason is about
the seller's own listing and is shown only to them, on the card of the listing
it is about; "etsy client error (400)" alone gave them nothing to act on.
"""

from __future__ import annotations

import json
import re

#: The step of the work a request belongs to, by method and path.
_STEPS: tuple[tuple[str, str, str], ...] = (
    ("POST", r"/shops/\d+/listings$", "creating the draft"),
    ("POST", r"/listings/\d+/images$", "adding an image"),
    ("DELETE", r"/listings/\d+/images/\d+$", "removing an image"),
    ("PUT", r"/listings/\d+/inventory$", "setting sizes, prices and SKU"),
    ("PUT", r"/listings/\d+/properties/\d+$", "setting a category attribute"),
    ("POST", r"/listings/\d+/personalization$", "setting the personalization question"),
    ("PATCH", r"/shops/\d+/listings/\d+$", "updating the listing"),
    ("POST", r"/shops/\d+/sections$", "creating a shop section"),
    ("GET", r"/shops/\d+/sections$", "reading the shop's sections"),
    ("GET", r"/listings/\d+$", "reading the listing back"),
    ("GET", r"/listings/\d+/images$", "reading the listing's images"),
)


def step_of(method: str | None, path: str | None) -> str | None:
    if not method or not path:
        return None
    for verb, pattern, name in _STEPS:
        if verb == method.upper() and re.search(pattern, path):
            return name
    return None


def _detail(body: str | None) -> str | None:
    """Etsy's one-line reason from a JSON error body ({"error": "..."}), if any."""
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("error") or parsed.get("error_description") or parsed.get("message")
    if not isinstance(text, str) or not text.strip():
        return None
    return " ".join(text.split())[:300]


class EtsyError(Exception):
    """Base class for Etsy API errors."""


class EtsyRateLimited(EtsyError):
    """HTTP 429. ``retry_after`` is seconds parsed from the Retry-After header."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("etsy rate limited (429)")
        self.retry_after = retry_after


class EtsyServerError(EtsyError):
    """HTTP 5xx. Transient; safe to retry."""

    def __init__(
        self,
        status_code: int = 500,
        *,
        body: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> None:
        super().__init__(f"etsy server error ({status_code})")
        self.status_code = status_code
        self.body = body
        self.path = path
        self.method = method

    @property
    def detail(self) -> str | None:
        """Etsy's own reason, when the response gave one."""
        return _detail(self.body)

    @property
    def step(self) -> str | None:
        return step_of(self.method, self.path)


class EtsyClientError(EtsyError):
    """HTTP 4xx other than 429. Permanent; not retried.

    ``body`` holds Etsy's response (which field it rejected) for diagnosis; it is
    logged server-side, not surfaced in ``job.last_error``.
    """

    def __init__(
        self,
        status_code: int = 400,
        *,
        body: str | None = None,
        path: str | None = None,
        method: str | None = None,
    ) -> None:
        super().__init__(f"etsy client error ({status_code})")
        self.status_code = status_code
        self.body = body
        self.path = path
        self.method = method

    @property
    def detail(self) -> str | None:
        """Etsy's own reason, when the response gave one."""
        return _detail(self.body)

    @property
    def step(self) -> str | None:
        return step_of(self.method, self.path)


def raise_for_etsy_status(
    status_code: int,
    retry_after: float | None = None,
    *,
    body: str | None = None,
    path: str | None = None,
    method: str | None = None,
) -> None:
    """Raise the appropriate :class:`EtsyError` for a non-2xx status code."""
    if status_code == 429:
        raise EtsyRateLimited(retry_after=retry_after)
    if 500 <= status_code < 600:
        raise EtsyServerError(status_code=status_code, body=body, path=path, method=method)
    if 400 <= status_code < 500:
        raise EtsyClientError(status_code=status_code, body=body, path=path, method=method)


def explain(exc: EtsyError) -> str:
    """What the seller is told when Etsy refused or failed a request."""
    if isinstance(exc, EtsyRateLimited):
        return "Etsy said requests were coming too fast and kept saying so; nothing is wrong with the listing"
    step = getattr(exc, "step", None)
    where = f" while {step}" if step else ""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if isinstance(exc, EtsyServerError):
        return f"Etsy had a problem of its own{where} (it answered {status}); nothing is wrong with the listing"
    if status == 404:
        return f"Etsy could not find the listing{where}; it may have been deleted in Shop Manager"
    if status in (401, 403):
        return f"Etsy refused access to this shop{where}; reconnect the shop in Shops, then try again"
    if detail:
        return f"Etsy refused it{where}: {detail}"
    return f"Etsy refused it{where} (it answered {status}) without saying why"
