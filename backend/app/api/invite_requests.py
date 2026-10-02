"""The public "Request an invite" form.

The only endpoint a visitor with no account can write to, so it is narrow:
three short fields, a ceiling per address and per day, and a field no person
fills in. What it stores is what the visitor typed and when; not their IP
address (that is used only for the ceiling, in Redis, for an hour).

Every answer is the same 202 whether the request was stored, was a repeat, or
was a bot's: the form never says whether an address is already known.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session
from app.core.ratelimit import client_ip
from app.db.models import InviteRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["invite-requests"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Requests one address may send in an hour, and all addresses together in a day.
PER_IP_PER_HOUR = 3
PER_DAY = 200


class InviteRequestIn(BaseModel):
    email: str = Field(max_length=254)
    shop: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)
    #: Hidden from people; anything in it marks an automated submission.
    website: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        cleaned = value.strip()
        if not EMAIL_RE.match(cleaned):
            raise ValueError("not a valid email address")
        return cleaned.casefold()


class Accepted(BaseModel):
    received: bool = True


async def _within(redis: Redis, key: str, limit: int, window: int) -> bool:
    now = int(time.time())
    bucket = f"{key}:{now - now % window}"
    try:
        count = int(await redis.incr(bucket))
        if count == 1:
            await redis.expire(bucket, window)
    except (RedisError, OSError):
        return True  # as the general limiter: a counter outage is not an outage
    return count <= limit


@router.post("/invite-requests", response_model=Accepted, status_code=202)
async def request_invite(
    body: InviteRequestIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Accepted:
    if body.website.strip():
        return Accepted()  # a bot: told the same as everyone, nothing stored
    if not await _within(redis, f"rl:invite:{client_ip(request)}", PER_IP_PER_HOUR, 3600):
        raise HTTPException(
            status_code=429, detail="Too many requests from this connection. Please try again in an hour."
        )
    if not await _within(redis, "rl:invite:all", PER_DAY, 86400):
        raise HTTPException(status_code=429, detail="We cannot take more requests today. Please try again tomorrow.")

    waiting = await session.scalar(
        select(func.count())
        .select_from(InviteRequest)
        .where(InviteRequest.email == body.email, InviteRequest.status == "pending")
    )
    if not waiting:
        session.add(
            InviteRequest(
                email=body.email,
                shop=" ".join(body.shop.split())[:200] or None,
                note=body.note.strip()[:1000] or None,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        logger.info("invite request received")
    return Accepted()
