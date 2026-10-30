"""Heavy image work, a little at a time and behind everything else.

Decoding and resizing a photo takes a CPU for a good part of a second, and analysing or printing on
a template photo for several. A seller's Upload page sends three at once; 25 sellers uploading
together gave each API process six threads resizing (the default thread pool), and pages waited
behind them for seconds (the load test, docs/load-test.md).

So this work has its own threads: at most ``settings.image_work_threads`` per process (production's
API processes run one each, the worker two), at a lower CPU priority (``settings.image_work_nice``). The rest wait their turn without holding a thread or a database
connection (``core/db_release.py``). When the CPUs are free it runs at full speed; when they are
busy, pages and Etsy requests go first and it takes the time that is left. Only these threads are
lowered: the worker's own event loop keeps its priority, so its drafts and go-lives keep pace.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from app.core import db_release
from app.core.config import get_settings

T = TypeVar("T")
logger = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _lower_priority() -> None:
    """Runs in each new thread; threads it starts (the image library's, the models') inherit it."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), get_settings().image_work_nice)
    except (OSError, AttributeError):  # not on this platform: only a priority
        logger.info("could not lower the priority of the image work threads")


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=get_settings().image_work_threads, thread_name_prefix="image-work",
                                       initializer=_lower_priority)
        return _pool


async def run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``function(*args, **kwargs)`` on the image threads, when its turn comes. Nothing it needs
    may be in an open transaction: this ends the task's idle one first."""
    await db_release.before_slow()
    call = functools.partial(contextvars.copy_context().run, function, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_executor(), call)
