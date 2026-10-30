"""Health check endpoint.

``/health`` answers 200 only when the API can reach its database and Redis (quality pass 2026-10,
review R18): the container healthcheck and ``deploy/update.sh`` rely on it, and a static "ok" let a
deploy whose database or Redis was unreachable count as healthy. Each check is bounded, so the
probe stays inside the healthcheck's 4 seconds; the answer names what failed, nothing more.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

#: Seconds each dependency has to answer.
CHECK_TIMEOUT = 1.5


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session, scope="function"), redis: Redis = Depends(get_redis)) -> JSONResponse:
    failed = []
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), CHECK_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - any failure means not healthy
        logger.warning("health: database unreachable (%s)", type(exc).__name__)
        failed.append("database")
    try:
        await asyncio.wait_for(redis.ping(), CHECK_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("health: redis unreachable (%s)", type(exc).__name__)
        failed.append("redis")
    if failed:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "failed": failed})
    return JSONResponse(content={"status": "ok"})
