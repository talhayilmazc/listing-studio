"""Redis commands of a process do not wait for each other.

redis-py before 6 holds the connection pool's lock across its check that a connection is ready to
send, and that check takes one turn of the event loop. So a process can start one Redis command per
turn of its event loop, however many are waiting. Every request runs several (its session, its rate
limit), so with 100 sellers at once this queue was where pages spent their time: an Overview p95 of
19 s while the database pool was not full (the load test, docs/load-test.md).

arq pins redis below 6, so the pool's own method is replaced here with what 6 does: take a connection
under no lock (``get_available_connection`` does not await, so nothing can come between its steps on
the event loop) and check it outside. A burst opens a few more connections, which then stay.
Installed on the class, because arq builds its own pool.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio.connection import ConnectionPool


async def _get_connection(self: ConnectionPool, command_name: Any = None, *keys: Any, **options: Any) -> Any:
    connection = self.get_available_connection()
    try:
        await self.ensure_connection(connection)
    except BaseException:
        await self.release(connection)
        raise
    return connection


def install() -> None:
    ConnectionPool.get_connection = _get_connection  # type: ignore[method-assign]
