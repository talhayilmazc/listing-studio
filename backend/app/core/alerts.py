"""One line to the operator, at the same address the deploy scripts use.

``ALERT_WEBHOOK_URL`` (an ntfy topic or a chat webhook) receives the text as the
request body, exactly as ``deploy/*.sh`` send it. Never raises: an alert that
cannot be delivered is logged, and the work that wanted to send it carries on.
The text is written by the caller and never contains a key, a token or a
seller's data.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def send(text: str) -> bool:
    url = get_settings().alert_webhook_url.strip()
    if not url:
        logger.error("ALERT (no ALERT_WEBHOOK_URL is set, so nobody was told): %s", text)
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(url, content=text.encode("utf-8"))
            response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 - an alert must never break what raised it
        logger.exception("alert could not be delivered")
        return False
