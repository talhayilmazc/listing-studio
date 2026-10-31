"""Heavy image work, a little at a time and behind everything else.

Decoding and resizing a photo takes a CPU for a good part of a second, and analysing or printing on
a template photo for several. A seller's Upload page sends three at once; 25 sellers uploading
together gave each API process six threads resizing (the default thread pool), and pages waited
behind them for seconds (the load test, docs/load-test.md).

So this work has its own threads: at most ``settings.image_work_threads`` per process, at a lower CPU
priority (``settings.image_work_nice``). (One per API process made uploads 3.5 times slower and pages no
faster: the load test.) The rest wait their turn without holding a thread or a database
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
import weakref
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from fastapi import UploadFile

from app.core import db_release
from app.core.config import get_settings

T = TypeVar("T")
logger = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_budgets: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Budget]" = weakref.WeakKeyDictionary()


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


class _Budget:
    """So many bytes, handed out first come, first served. A file larger than the whole budget
    waits until it can have all of it, and then goes alone."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.free = total
        self._waiting: deque[tuple[int, asyncio.Future[None]]] = deque()

    async def take(self, size: int) -> int:
        size = min(size, self.total)
        if not self._waiting and self.free >= size:
            self.free -= size
            return size
        turn: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (size, turn)
        self._waiting.append(entry)
        try:
            await turn
        except asyncio.CancelledError:
            if entry in self._waiting:
                self._waiting.remove(entry)
                self._next()  # those behind it may fit now
            elif not turn.cancelled():  # given its bytes in the same moment it was cancelled
                self.give(size)
            raise
        return size

    def give(self, size: int) -> None:
        self.free += size
        self._next()

    def _next(self) -> None:
        while self._waiting and self.free >= self._waiting[0][0]:
            size, turn = self._waiting.popleft()
            if not turn.done():
                self.free -= size
                turn.set_result(None)


def _budget() -> _Budget:
    loop = asyncio.get_running_loop()
    budget = _budgets.get(loop)
    if budget is None:
        budget = _budgets[loop] = _Budget(get_settings().upload_memory_mb * 1024 * 1024)
    return budget


async def upload_turn(file: UploadFile) -> AsyncIterator[None]:
    """A dependency of a route that reads a whole file and processes it: at most
    ``settings.upload_memory_mb`` of uploaded files in this process's memory at once. A waiting upload
    is on disk (over 1 MB: the multipart parser spools it), so 300 sellers' photos of 30 MB cannot fill
    the API's memory while they wait for a thread. List it before the session: nothing is held waiting.

    Bytes, not a number of files: the route holds its turn through its waits for a database
    connection too, and with four files per process at a turn a full pool cut uploads to a trickle
    (the load test: 1,104 of 1,500 timed out at 120 s, docs/load-test.md)."""
    budget = _budget()
    size = await budget.take(file.size or 0)
    try:
        yield
    finally:
        budget.give(size)


async def run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``function(*args, **kwargs)`` on the image threads, when its turn comes. Nothing it needs
    may be in an open transaction: this ends the task's idle one first."""
    await db_release.before_slow()
    call = functools.partial(contextvars.copy_context().run, function, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_executor(), call)
