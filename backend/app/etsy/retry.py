"""Retry backoff policy for requeued jobs."""

from __future__ import annotations

BASE_SECONDS: float = 2.0
MAX_BACKOFF_SECONDS: float = 32.0


def backoff_seconds(attempt: int, *, base: float = BASE_SECONDS, cap: float = MAX_BACKOFF_SECONDS) -> float:
    """Exponential backoff: 2, 4, 8, 16, 32 (capped) for attempts 1, 2, 3, ...

    ``attempt`` is the 1-based number of the attempt that just failed.
    """
    if attempt < 1:
        attempt = 1
    return min(base**attempt, cap)
