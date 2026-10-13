"""Two operator settings kept in ``app_setting`` and changed in Admin.

* **How long analytics history is kept** (``analytics_retention`` =
  ``{"months": n}``; default :data:`ANALYTICS_MONTHS`): the daily sales totals
  and order lines, the daily fee and ad totals from the payment ledger, what
  "Import from Etsy" stored, the daily views and favourites of each listing, and
  the text versions that have ended. The retention job deletes what is older
  (workers/retention.py); the Privacy Policy shows the figure in force.
* **New shop connections paused** (``connections_paused`` = ``{"on": bool}``;
  off by default): while on, no new shop can be connected and the Shops page
  says why. Shops already connected keep working, and one of them can still be
  reconnected (to grant a permission).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

logger = logging.getLogger(__name__)

ANALYTICS_KEY = "analytics_retention"
#: Default months of analytics history kept.
ANALYTICS_MONTHS = 24
ANALYTICS_MIN_MONTHS, ANALYTICS_MAX_MONTHS = 1, 60
#: A month for this purpose: 365.25 / 12 days, rounded up over the whole period.
DAYS_PER_MONTH = 30.4375

PAUSE_KEY = "connections_paused"
#: Shown on the Shops page while new connections are paused.
PAUSE_NOTICE = (
    "New shop connections are paused until Etsy approves our commercial access. "
    "Your existing shops keep working."
)


def validate_months(value: Any) -> int:
    try:
        months = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("months must be a whole number") from exc
    if not ANALYTICS_MIN_MONTHS <= months <= ANALYTICS_MAX_MONTHS:
        raise ValueError(f"months must be between {ANALYTICS_MIN_MONTHS} and {ANALYTICS_MAX_MONTHS}")
    return months


async def analytics_months(session: AsyncSession) -> int:
    row = await session.get(AppSetting, ANALYTICS_KEY)
    if row is None:
        return ANALYTICS_MONTHS
    try:
        return validate_months((row.value or {}).get("months"))
    except ValueError:
        logger.error("app_setting %s is not usable; using the default", ANALYTICS_KEY)
        return ANALYTICS_MONTHS


def analytics_days(months: int) -> int:
    return int(-(-months * DAYS_PER_MONTH // 1))  # rounded up: never a day short of the months promised


def analytics_cutoff(months: int, now: datetime | None = None) -> date:
    """The oldest day of analytics history still kept."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=analytics_days(months))).date()


async def set_analytics_months(session: AsyncSession, months: int) -> int:
    months = validate_months(months)
    row = await session.get(AppSetting, ANALYTICS_KEY)
    if row is None:
        session.add(AppSetting(key=ANALYTICS_KEY, value={"months": months}))
    else:
        row.value = {"months": months}
    return months


async def connections_paused(session: AsyncSession) -> bool:
    row = await session.get(AppSetting, PAUSE_KEY)
    return bool(row is not None and (row.value or {}).get("on"))


async def set_connections_paused(session: AsyncSession, on: bool) -> bool:
    row = await session.get(AppSetting, PAUSE_KEY)
    if row is None:
        session.add(AppSetting(key=PAUSE_KEY, value={"on": bool(on)}))
    else:
        row.value = {"on": bool(on)}
    return bool(on)
