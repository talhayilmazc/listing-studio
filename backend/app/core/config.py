"""Application settings, loaded from environment / .env.

Secrets are never hardcoded here — only read from the environment (see CLAUDE.md).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"

    # Database (async URL, e.g. postgresql+asyncpg://...).
    database_url: str = "postgresql+asyncpg://etsy:change-me@postgres:5432/etsy_assistant"

    # Redis / arq broker.
    redis_url: str = "redis://redis:6379/0"

    # Security — used later for encrypted token storage. Never logged or returned.
    secret_key: str = "change-me"
    encryption_key: str = "change-me"

    # Etsy Open API v3 credentials (empty until the OAuth step).
    etsy_client_id: str = ""
    etsy_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
