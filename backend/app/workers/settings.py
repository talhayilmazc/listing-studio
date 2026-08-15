"""arq worker settings.

Placeholder: no jobs are registered yet. Every Etsy API call will flow through
this queue in later steps (see the architecture rule in CLAUDE.md).
"""

from arq.connections import RedisSettings

from app.core.config import get_settings


class WorkerSettings:
    """arq worker configuration."""

    functions: list = []
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
