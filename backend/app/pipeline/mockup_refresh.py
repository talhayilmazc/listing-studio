"""Which mockups of a run are being made again, and which could not be (seller feedback: the
results update live while the design editor stays open).

The seller saves an edit, then applies it to all photos while the first mockup is still being
made: the second request used to be refused ("The mockups are still being made.") and its
mockups never changed. Now every request adds its (design, photo) pairs to the run's queue in
Redis, and one worker job per run takes them in turns until the queue is empty:

* ``mockup-refresh:{run}``         the pairs waiting or being made, each with the version
  (``OutputOut.stamp``) its mockup had when it was asked for: its tile is "Updating…" until the
  version changes, so each tile swaps in as soon as its own mockup is made;
* ``mockup-refresh-failed:{run}``  the pairs whose mockup could not be made, with the reason;
* ``mockup-refresh-worker:{run}``  set while a worker job owns the run's queue;
* ``mockup-refresh-again:{run}``   pairs asked for again while their turn was being made: they
  stay queued for the next turn (the turn under way may have read the earlier edit).

Adding pairs and deciding whether a job must be queued is one atomic step, and so is a worker
deciding it is finished: a request that arrives while the last pairs are being made is never
lost and never starts a second job. The run's results (``RunOut.refreshing``,
``RunOut.refresh_failed``) are read from here, so the page and the editor show the same state.
Kept a day at most; nothing here is Etsy data.
"""

from __future__ import annotations

import uuid
from typing import Any

#: How long a queue, its failures and a worker's claim are kept without activity (seconds).
QUEUE_TTL = 24 * 3600
#: A worker's claim on a run: renewed after each turn; longer than one job may take
#: (WORKER_JOB_TIMEOUT 900 s), so a dead worker's claim lapses and the next request starts one.
CLAIM_TTL = 960

#: Add pairs to the queue (ARGV[3..]: member, version, member, version, …; a pair already queued
#: is marked to go again and takes the newer version), clear their old failures, and claim the
#: run when no worker has it (ARGV[1]: the queue's lifetime, ARGV[2]: the claim's). Returns 1 when
#: the caller must queue a worker job.
_ADD = """
for i = 3, #ARGV, 2 do
  if redis.call('HEXISTS', KEYS[1], ARGV[i]) == 1 then redis.call('SADD', KEYS[4], ARGV[i]) end
  redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
  redis.call('HDEL', KEYS[2], ARGV[i])
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
redis.call('EXPIRE', KEYS[4], tonumber(ARGV[1]))
if redis.call('EXISTS', KEYS[3]) == 1 then return 0 end
redis.call('SET', KEYS[3], '1', 'EX', tonumber(ARGV[2]))
return 1
"""

#: A turn is over: each pair (ARGV) leaves the queue unless it was asked for again meanwhile.
#: Returns the pairs kept for the next turn.
_DONE = """
local kept = {}
for i = 1, #ARGV do
  if redis.call('SREM', KEYS[2], ARGV[i]) == 1 then table.insert(kept, ARGV[i])
  else redis.call('HDEL', KEYS[1], ARGV[i]) end
end
return kept
"""

#: A worker that found the queue empty lets go of the run, unless pairs arrived meanwhile.
#: Returns 1 when it may stop.
_FINISH = """
if redis.call('HLEN', KEYS[1]) == 0 then
  redis.call('DEL', KEYS[2])
  return 1
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[1]))
return 0
"""


def _queue(run_id: Any) -> str:
    return f"mockup-refresh:{run_id}"


def _failed(run_id: Any) -> str:
    return f"mockup-refresh-failed:{run_id}"


def _claim(run_id: Any) -> str:
    return f"mockup-refresh-worker:{run_id}"


def _again(run_id: Any) -> str:
    return f"mockup-refresh-again:{run_id}"


def _text(raw: Any) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def member(design_id: Any, photo_id: Any) -> str:
    return f"{design_id}:{photo_id}"


def parse(raw: Any) -> tuple[uuid.UUID, uuid.UUID]:
    design, photo = _text(raw).split(":", 1)
    return uuid.UUID(design), uuid.UUID(photo)


async def add(redis: Any, run_id: Any, pairs: dict[tuple[Any, Any], str]) -> bool:
    """Queue the pairs (each with its mockup's version now; "" when it has none yet) for the
    run. True: no worker owns the run, queue a job now."""
    if not pairs:
        return False
    args: list[str] = []
    for (d, p), stamp in pairs.items():
        args += [member(d, p), stamp or ""]
    started = await redis.eval(_ADD, 4, _queue(run_id), _failed(run_id), _claim(run_id), _again(run_id),
                               QUEUE_TTL, CLAIM_TTL, *args)
    return int(started) == 1


async def claimed(redis: Any, run_id: Any) -> bool:
    """A worker job owns the run's queue (a re-render is under way)."""
    return bool(await redis.exists(_claim(run_id)))


async def take(redis: Any, run_id: Any) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """The pairs waiting now (left in the queue until :func:`done`)."""
    return sorted(parse(m) for m in await redis.hkeys(_queue(run_id)))


async def done(redis: Any, run_id: Any, pairs: list[tuple[Any, Any]], failed: dict[tuple[Any, Any], str]) -> None:
    """This turn is over: its pairs leave the queue (unless asked for again meanwhile: they go in
    the next turn, and are not called failed); those that could not be made are kept with why."""
    kept: set[str] = set()
    if pairs:
        raw = await redis.eval(_DONE, 2, _queue(run_id), _again(run_id), *[member(d, p) for d, p in pairs])
        kept = {_text(k) for k in raw or []}
    failed = {pair: why for pair, why in failed.items() if member(*pair) not in kept}
    if failed:
        await redis.hset(_failed(run_id), mapping={member(d, p): reason[:300] for (d, p), reason in failed.items()})
        await redis.expire(_failed(run_id), QUEUE_TTL)
    await redis.expire(_claim(run_id), CLAIM_TTL)


async def finish(redis: Any, run_id: Any) -> bool:
    """True when the queue is empty and the worker has let go of the run."""
    return int(await redis.eval(_FINISH, 2, _queue(run_id), _claim(run_id), CLAIM_TTL)) == 1


async def state(redis: Any, run_id: Any) -> tuple[dict[tuple[uuid.UUID, uuid.UUID], str], dict[tuple[uuid.UUID, uuid.UUID], str]]:
    """(pairs being made again with the version they had when asked for, pairs that could not be
    made with the reason)."""
    waiting = {parse(k): _text(v) for k, v in (await redis.hgetall(_queue(run_id))).items()}
    failed = {parse(k): _text(v) for k, v in (await redis.hgetall(_failed(run_id))).items()}
    return waiting, failed
