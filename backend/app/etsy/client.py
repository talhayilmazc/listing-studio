"""Etsy client interface.

The concrete HTTP client (taxonomy, listing, image, inventory) is built in
work-order step 4. This step only needs the *interface* the worker calls
through, plus a placeholder that fails loudly if invoked before then. Per the
architecture rule, only the worker ever calls this; the service layer enqueues
jobs instead of talking to Etsy directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.db.models import JobType

# Etsy API v3 base URL.
ETSY_API_BASE = "https://api.etsy.com/v3"


def api_key_header(client_id: str, shared_secret: str) -> str:
    """Value for the ``x-api-key`` header on Etsy API v3 requests.

    VERIFIED against a live Personal App: the header must be
    ``{keystring}:{shared_secret}`` -- sending the keystring alone makes
    ``/application/openapi-ping`` return 403. Both come from ``.env``
    (``ETSY_CLIENT_ID`` = keystring, ``ETSY_CLIENT_SECRET`` = shared secret) and
    are never logged.
    """
    return f"{client_id}:{shared_secret}"


def auth_headers(client_id: str, shared_secret: str, access_token: str) -> dict[str, str]:
    """Headers for an authenticated Etsy API v3 request."""
    return {
        "x-api-key": api_key_header(client_id, shared_secret),
        "Authorization": f"Bearer {access_token}",
    }


@runtime_checkable
class EtsyClient(Protocol):
    """Executes a queued job against the Etsy Open API v3."""

    async def execute(self, job_type: JobType, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform the API call for ``job_type`` and return the parsed response."""
        ...


class UnavailableEtsyClient:
    """Stand-in used until the real client lands in step 4."""

    async def execute(self, job_type: JobType, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "Etsy client is implemented in work-order step 4; "
            "inject a real EtsyClient before processing jobs."
        )
