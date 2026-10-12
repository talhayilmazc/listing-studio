"""A development switch that keeps this process from calling Etsy at all.

A development database can hold real sellers' shops and their tokens while
production is already serving those same shops. A local worker must then do
nothing on Etsy for them: no sync, no stats read, no draft, no publish, and no
token refresh (a refresh replaces the refresh token, which could cut off the
copy production holds).

With ``ETSY_CALLS_BLOCKED=true`` (``settings.etsy_calls_blocked``) every
outbound call is refused **before any network I/O**, at the few places all of
them pass through:

* the API v3 client (``EtsyApiClient._request``): every job's requests;
* the OAuth token endpoint (``oauth.exchange_code``, ``oauth.refresh_tokens``);
* fetching a listing's image from Etsy's image host;
* starting a shop connection (the browser is not sent to Etsy to authorise).

Off by default, so production is unchanged; ``docker-compose.yml`` turns it on
for local work. A job that needs Etsy then fails with this error and changes
nothing: no token is touched and no shop is disconnected.
"""

from __future__ import annotations

from app.core.config import get_settings

MESSAGE = (
    "Etsy calls are blocked in this environment (ETSY_CALLS_BLOCKED=true): "
    "nothing was sent to Etsy."
)


class EtsyCallsBlocked(RuntimeError):
    """An outbound Etsy call was refused by the development switch."""

    def __init__(self, what: str = "") -> None:
        super().__init__(MESSAGE)
        #: What was about to be called (a method and path, "token refresh", ...). Never a token.
        self.what = what


def blocked() -> bool:
    return bool(get_settings().etsy_calls_blocked)


def check(what: str = "") -> None:
    """Raise :class:`EtsyCallsBlocked` when this environment may not call Etsy."""
    if blocked():
        raise EtsyCallsBlocked(what)
