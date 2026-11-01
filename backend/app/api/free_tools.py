"""The optional AI title and tag ideas on the public site (docs/free-tools.md).

The one free tool that spends money, so it is off by default and narrow when on:

* ``FREE_TOOL_AI_ENABLED`` off, or a Turnstile key missing: the status says so and the POST is a 404.
* 3 a day per visitor (``FREE_TOOL_AI_PER_VISITOR``). A visitor is their connection, hashed with our
  secret: the address is not stored, and the count is gone after ``VISITOR_DAYS``. A call that fails
  on our side gives the use back.
* Cloudflare Turnstile before every call.
* A daily cost cap (``FREE_TOOL_AI_DAILY_CAP_USD``): the day's ``free_tool`` rows in ``ai_call`` add up
  to it, the tool stops itself until the next UTC day and the operators hear once. Calls already under
  way can pass the cap by what they cost, a fraction of a cent each.

Nothing the visitor typed is stored or logged, and no answer carries a token count or a cost.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session
from app.compliance import trademarks
from app.core import ai_meter, alerts, llm_status, turnstile
from app.core.config import get_settings
from app.core.llm_status import LLMUnavailable
from app.core.ratelimit import client_ip
from app.db.models import AiCall
from app.pipeline import listing_ideas
from app.pipeline.llm import LLMError, client_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/free-tools", tags=["free-tools"])

PURPOSE = "free_tool"
#: How long a visitor's count stays (Redis): past the end of the UTC day it belongs to.
VISITOR_DAYS = 2
MIN_DESCRIPTION = 10
MAX_DESCRIPTION = 300

SPENT = "Today's free ideas have run out. They come back after 00:00 UTC. The title and tag checkers keep working."
BUSY = "We could not write ideas just now, and this try was not counted. Please try again in a little while."
USED_UP = "You have used today's free ideas. They come back after 00:00 UTC. The title and tag checkers keep working."
NOT_A_PERSON = "We could not confirm you are a person. Please try the check again."


def is_open() -> bool:
    settings = get_settings()
    return settings.free_tool_ai_enabled and bool(settings.turnstile_site_key and settings.turnstile_secret_key)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _visitor_key(request: Request, day: date) -> str:
    hashed = hashlib.sha256(f"{get_settings().secret_key}:{client_ip(request)}".encode()).hexdigest()[:32]
    return f"free-tool:ideas:{day.isoformat()}:{hashed}"


async def spent_today(session: AsyncSession, day: date) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(AiCall.cost_usd), 0)).where(AiCall.day == day, AiCall.purpose == PURPOSE)
    )
    return float(total or 0)


async def _over_cap(session: AsyncSession, redis: Redis, day: date) -> bool:
    """Whether the day's budget is gone. The operators hear once a day."""
    cap = get_settings().free_tool_ai_daily_cap_usd
    spent = await spent_today(session, day)
    if spent < cap:
        return False
    try:
        first = await redis.set(f"free-tool:cap-alert:{day.isoformat()}", "1", nx=True, ex=VISITOR_DAYS * 86400)
    except (RedisError, OSError):
        first = False
    if first:
        await alerts.send(f"Listyro: the free AI ideas tool reached its daily cost cap (${cap:.2f}) and is off until 00:00 UTC.")
    return True


class IdeasStatus(BaseModel):
    available: bool
    #: Why not: "off" (not switched on) or "spent" (the day's budget is gone).
    reason: str | None = None
    message: str | None = None
    site_key: str | None = None
    remaining: int = 0
    per_day: int = 0


class IdeasIn(BaseModel):
    description: str = Field(min_length=MIN_DESCRIPTION, max_length=MAX_DESCRIPTION)
    turnstile_token: str = Field(min_length=1, max_length=4096)


class IdeasOut(BaseModel):
    titles: list[str]
    tags: list[str]
    remaining: int


@router.get("/ideas", response_model=IdeasStatus)
async def ideas_status(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    redis: Redis = Depends(get_redis),
) -> IdeasStatus:
    if not is_open():
        return IdeasStatus(available=False, reason="off")
    settings = get_settings()
    day = _today()
    if await _over_cap(session, redis, day):
        return IdeasStatus(available=False, reason="spent", message=SPENT)
    try:
        used = int(await redis.get(_visitor_key(request, day)) or 0)
    except (RedisError, OSError):
        return IdeasStatus(available=False, reason="spent", message=BUSY)
    per_day = settings.free_tool_ai_per_visitor
    return IdeasStatus(available=True, site_key=settings.turnstile_site_key, remaining=max(0, per_day - used), per_day=per_day)


@router.post("/ideas", response_model=IdeasOut)
async def write_ideas(
    body: IdeasIn,
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
    redis: Redis = Depends(get_redis),
) -> IdeasOut:
    if not is_open():
        raise HTTPException(status_code=404, detail="Not found")
    settings = get_settings()
    day = _today()
    if await _over_cap(session, redis, day):
        raise HTTPException(status_code=503, detail=SPENT)
    if await llm_status.current(redis):
        raise HTTPException(status_code=503, detail=BUSY)
    # A brand in the description is said before anything is spent: we do not write around one.
    brands = list(dict.fromkeys(s.term for s in trademarks.all_marks().spot(body.description)))
    if brands:
        raise HTTPException(status_code=422, detail=f"Please take out {', '.join(brands)}: we do not write ideas around a brand name. Nothing was counted.")

    key = _visitor_key(request, day)
    limit = settings.free_tool_ai_per_visitor
    try:
        if int(await redis.get(key) or 0) >= limit:
            raise HTTPException(status_code=429, detail=USED_UP)
    except (RedisError, OSError):
        raise HTTPException(status_code=503, detail=BUSY) from None  # this guards a budget: no count, no call
    if not await turnstile.verified(body.turnstile_token):
        raise HTTPException(status_code=403, detail=NOT_A_PERSON)
    try:
        used = int(await redis.incr(key))
        if used == 1:
            await redis.expire(key, VISITOR_DAYS * 86400)
    except (RedisError, OSError):
        raise HTTPException(status_code=503, detail=BUSY) from None
    if used > limit:
        await _give_back(redis, key)
        raise HTTPException(status_code=429, detail=USED_UP)

    try:
        async with ai_meter.scope(None, session):
            with ai_meter.purpose(PURPOSE):
                ideas = await listing_ideas.write(client_for(settings, PURPOSE), body.description)
    except LLMUnavailable as exc:
        await llm_status.report(redis, exc)
        await _give_back(redis, key)
        raise HTTPException(status_code=503, detail=BUSY) from None
    except LLMError:
        logger.warning("free tool: the model's answer could not be used")
        await _give_back(redis, key)
        raise HTTPException(status_code=503, detail=BUSY) from None
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - a visitor gets a plain sentence, and the use back
        logger.warning("free tool: the model call failed: %s", type(exc).__name__)
        await _give_back(redis, key)
        raise HTTPException(status_code=503, detail=BUSY) from None
    if not ideas.titles and not ideas.tags:
        await _give_back(redis, key)
        raise HTTPException(status_code=503, detail=BUSY)
    await _over_cap(session, redis, day)  # tell the operators now, not on the next visit
    return IdeasOut(titles=ideas.titles, tags=ideas.tags, remaining=max(0, limit - used))


async def _give_back(redis: Redis, key: str) -> None:
    try:
        await redis.decr(key)
    except (RedisError, OSError):
        logger.warning("free tool: a use could not be given back")
