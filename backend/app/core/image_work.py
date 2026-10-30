"""Heavy image work inside an API request, a little at a time.

Decoding and resizing a photo takes a CPU for a good part of a second. A seller's Upload page sends
three at once; 25 sellers uploading together gave each API process six threads resizing (the
default thread pool), and pages waited behind them for seconds (the load test, docs/load-test.md).
At most :data:`AT_ONCE` run per process; the rest wait their turn without holding a thread or a
database connection, so an upload takes a little longer and every page stays quick.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

#: Per API process (two of them in production).
AT_ONCE = 2

_turns: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _turn() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    turn = _turns.get(loop)
    if turn is None:
        turn = _turns[loop] = asyncio.Semaphore(AT_ONCE)
    return turn


async def run(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``function(*args, **kwargs)`` off the event loop, when its turn comes."""
    async with _turn():
        return await asyncio.to_thread(function, *args, **kwargs)
