"""Cloudflare Turnstile: is this visitor a person? Asked once for each thing that costs money.

Only the token and our secret go to Cloudflare. A failure to get an answer counts as "no": this guards
a budget, so it fails closed.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 8.0


async def verified(token: str, client: httpx.AsyncClient | None = None) -> bool:
    secret = get_settings().turnstile_secret_key
    if not secret or not token:
        return False
    own = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await http.post(VERIFY_URL, data={"secret": secret, "response": token})
        return response.status_code == 200 and response.json().get("success") is True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("turnstile could not be asked: %s", type(exc).__name__)
        return False
    finally:
        if own:
            await http.aclose()
