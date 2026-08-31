"""Etsy API error taxonomy and HTTP-status classification.

These drive the worker retry policy:
- 429              -> retryable, honour ``Retry-After`` if present
- 5xx              -> retryable with exponential backoff
- 4xx (except 429) -> permanent failure, no retry

The exception ``str()`` stays generic (status code only) so no user data or token
can leak into ``job.last_error`` or the UI. The rejected-field detail Etsy returns
(``body``) and the request ``path``/``method`` are carried as attributes for
**server-side logging only** — they never contain a token (tokens ride request
headers, not the response body or URL).
"""

from __future__ import annotations


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
