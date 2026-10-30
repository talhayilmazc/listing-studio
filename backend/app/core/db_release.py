"""No database connection is held across slow work.

A session that has read something holds a pooled connection, in a transaction, until it commits.
Awaiting something slow with one open (a model answer: tens of seconds; an Etsy request behind the
rate limiter; an image being analysed) keeps that connection from everyone else: 25 sellers writing
listings took the whole pool and every page of every seller waited (the load test,
docs/load-test.md).

Rather than ask each of the many paths to remember, the few places all slow work passes through
call :func:`before_slow`: the Etsy client (``etsy/api.py``), the model client (``pipeline/llm.py``)
and heavy image work (``core/image_work.py``). It ends the current task's transaction **only if
nothing can be lost by it**: nothing pending, nothing written and not yet committed, no row lock
taken, no savepoint. Reads are all it gives up, and under READ COMMITTED (the default, and all this
app uses) ending a read-only transaction changes nothing the code can see. Objects stay usable:
it skips a session that would expire them on commit. A session holding a write or a lock keeps its
transaction, on purpose; that is what the transaction is for.

A task's own sessions only, never another task's (which may be in the middle of a query).
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.util import greenlet_spawn

#: Which sessions each running task has a transaction open on.
_open: "weakref.WeakKeyDictionary[asyncio.Task, set[Session]]" = weakref.WeakKeyDictionary()


def _task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # not on an event loop: a script's synchronous session
        return None


@event.listens_for(Session, "after_begin")
def _began(session: Session, transaction: Any, connection: Any) -> None:
    task = _task()
    if task is not None:
        _open.setdefault(task, set()).add(session)


@event.listens_for(Session, "after_flush")
def _wrote(session: Session, flush_context: Any) -> None:
    session.info["db_release_wrote"] = True


@event.listens_for(Session, "do_orm_execute")
def _executing(state: Any) -> None:
    if getattr(state.statement, "_for_update_arg", None) is not None:
        state.session.info["db_release_locked"] = True


@event.listens_for(Session, "after_transaction_end")
def _ended(session: Session, transaction: Any) -> None:
    if transaction.parent is not None:
        return  # a savepoint or a subtransaction: the transaction goes on
    session.info.pop("db_release_wrote", None)
    session.info.pop("db_release_locked", None)
    task = _task()
    if task is not None and task in _open:
        _open[task].discard(session)


def _holds_something(session: Session) -> bool:
    return bool(
        session.new or session.dirty or session.deleted
        or session.info.get("db_release_wrote") or session.info.get("db_release_locked")
        or session.in_nested_transaction()
        or session.expire_on_commit
    )


async def before_slow() -> int:
    """End this task's transactions that hold nothing; returns how many it ended."""
    task = _task()
    sessions = _open.get(task) if task is not None else None
    if not sessions:
        return 0
    released = 0
    for session in list(sessions):
        if session.in_transaction() and not _holds_something(session):
            await greenlet_spawn(session.commit)
            released += 1
    return released
