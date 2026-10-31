"""``python -m app.cli health-digest [--send]`` and ``alert-test``.

The digest as the 09:00 message would say it, now. Without ``--send`` it only prints and reads
nothing but the database and Redis: no Etsy call, nothing written. ``alert-test`` sends one line to
ALERT_WEBHOOK_URL so the operator can see it arrive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core import alerts
from app.core.config import get_settings


async def _digest(send: bool) -> tuple[str, bool]:
    from redis.asyncio import Redis

    from app.db.session import get_sessionmaker
    from app.monitor import digest

    redis = Redis.from_url(get_settings().redis_url)
    try:
        async with get_sessionmaker()() as session:
            text = await digest.build(session, redis, datetime.now(timezone.utc), take_deferred=False)
        delivered = await alerts.send(text) if send else False
        if delivered:
            from app.monitor import notify

            await notify.take_deferred(redis)
    finally:
        await redis.aclose()
    return text, delivered


def run_digest(send: bool) -> int:
    text, delivered = asyncio.run(_digest(send))
    print(text)
    if send:
        print("\nSent." if delivered else "\nNOT sent: see the log (is ALERT_WEBHOOK_URL set?).")
        return 0 if delivered else 1
    return 0


def run_alert_test() -> int:
    if not get_settings().alert_webhook_url.strip():
        print("ALERT_WEBHOOK_URL is not set: nothing to send to.")
        return 1
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if asyncio.run(alerts.send(f"[Listyro] Test alert, {when}. If you can read this, alerts reach you.")):
        print("Sent. Check that it arrived.")
        return 0
    print("NOT delivered: see the log.")
    return 1
