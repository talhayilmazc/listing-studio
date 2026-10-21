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


def shape_of(value: object) -> object:
    """What a request carried, without its text: field names and the kind (and length) of
    each value. ``{"personalization_questions": [{"question_text": "Your name", ...}]}``
    becomes ``{"personalization_questions": {"list": 1, "item": {"question_text": "string(9)", ...}}}``.
    Kept with a failed job for diagnosis (``python -m app.cli draft-failures``); never a
    token (tokens ride headers) and never the seller's words."""
    if isinstance(value, dict):
        return {str(k): shape_of(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return {"list": len(value), "item": shape_of(value[0])} if value else "list(0)"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return f"string({len(value)})"
    if value is None:
        return "null"
    return type(value).__name__


class EtsyError(Exception):
    """Base class for Etsy API errors. ``sent``: the shape of what the refused request
    carried (``shape_of``), set by the client."""

    sent: object = None


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


def record(exc: "EtsyError") -> dict:
    """What a failed job keeps about Etsy's refusal: status, step, method and path (ids
    only), Etsy's own one-line reason and the shape of what was sent. No token, no buyer
    data, none of the seller's words."""
    return {
        "status": getattr(exc, "status_code", 429 if isinstance(exc, EtsyRateLimited) else None),
        "step": getattr(exc, "step", None),
        "method": getattr(exc, "method", None),
        "path": getattr(exc, "path", None),
        "message": getattr(exc, "detail", None),
        "sent": getattr(exc, "sent", None),
    }


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


#: Etsy's refusal of another shop's id ("Could not find readiness_state_id='1402915464979'
#: associated with shop '54783739'"): the setting it names, as the seller knows it.
_FOREIGN = re.compile(r"could not find (\w+?)(?:_id)?s?\s*=?\s*'?\d*'?\s+associated with shop", re.IGNORECASE)
_SETTING = {
    "readiness_state": "processing profile",
    "shipping_profile": "shipping profile",
    "return_policy": "return policy",
    "production_partner": "production partner",
    "shop_section": "shop section",
}


def foreign_setting(detail: str | None) -> str | None:
    """The shop setting an Etsy refusal says this shop does not have, if it says that."""
    match = _FOREIGN.search(detail or "")
    if not match:
        return None
    return _SETTING.get(match.group(1).lower(), match.group(1).replace("_", " "))


def explain(exc: EtsyError, shop: str | None = None) -> str:
    """What the seller is told when Etsy refused or failed a request. ``shop``: the
    shop's name, when the refusal is about one of that shop's own settings."""
    if isinstance(exc, EtsyRateLimited):
        return "Etsy said requests were coming too fast and kept saying so; nothing is wrong with the listing"
    step = getattr(exc, "step", None)
    where = f" while {step}" if step else ""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if isinstance(exc, EtsyServerError):
        return f"Etsy had a problem of its own{where} (it answered {status}); nothing is wrong with the listing"
    if detail and "calculated shipping profile" in detail.lower():
        # The reference has no weight or size for its calculated shipping profile.
        from app.pipeline.reference import SIZE_FIX

        return SIZE_FIX
    setting = foreign_setting(detail)
    if setting:
        name = shop or "This shop"
        refused = f" (Etsy refused it{where})" if where else ""
        return (f"{name} does not have the {setting} this listing used; set it up in {shop or 'that shop'}, "
                f"then finish the draft{refused}")
    if status == 404 and detail and ("_id" in detail or "associated with" in detail.lower()):
        # Not the listing: one of the ids sent with it (Etsy answers 404 for those too).
        return f"Etsy refused it{where}: {detail}"
    if status == 404:
        return f"Etsy could not find the listing{where}; it may have been deleted in Shop Manager"
    if status in (401, 403):
        return f"Etsy refused access to this shop{where}; reconnect the shop in Shops, then try again"
    if detail:
        return f"Etsy refused it{where}: {detail}"
    return f"Etsy refused it{where} (it answered {status}) without saying why"
