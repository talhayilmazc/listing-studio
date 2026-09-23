"""Application settings, loaded from environment / .env.

Secrets are never hardcoded here — only read from the environment (see CLAUDE.md).
"""

from functools import lru_cache

from pydantic import Field
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

    # Invite-code minting and admin password resets (production-spec A1/A4).
    # Empty disables the admin endpoints entirely rather than leaving them open.
    admin_token: str = ""

    # Session cookie lifetime, days (A3). Rolling: renewed on every request.
    session_ttl_days: int = 30
    # Send the session cookie only over HTTPS. Must be true in production.
    session_cookie_secure: bool = False

    # Per-tenant daily Etsy budget (production-spec C: 5 tenants x 1000 = 5000).
    tenant_daily_quota: int = 1000

    # --- Hardening (production-spec D) --------------------------------------
    # Header carrying the real client IP. Empty = the socket peer. Behind
    # Cloudflare set "cf-connecting-ip": Cloudflare overwrites it, whereas the
    # first X-Forwarded-For entry is whatever the client chose to send.
    client_ip_header: str = ""

    # Request ceilings, per client per window. Auth endpoints (login, register,
    # admin) get their own much tighter budget on top of the login lockout.
    rate_limit_requests: int = 1200
    rate_limit_window_seconds: int = 60
    auth_rate_limit_requests: int = 20
    auth_rate_limit_window_seconds: int = 15 * 60

    # Upload limits. Type is decided from the bytes, never the filename.
    max_upload_bytes: int = 25 * 1024 * 1024  # per file
    max_batch_bytes: int = 1024 * 1024 * 1024  # per batch, all files
    max_image_pixels: int = 60_000_000  # decompression-bomb ceiling (~7746 x 7746)

    # Optional error reporting (production-spec F6). Empty = off, and then the
    # Privacy Policy does not mention Sentry, because nothing is sent to it.
    sentry_dsn: str = ""

    # Etsy Open API v3 credentials. etsy_client_id = keystring, etsy_client_secret
    # = shared secret. The x-api-key header is "{keystring}:{shared_secret}".
    etsy_client_id: str = ""
    etsy_client_secret: str = ""

    # Etsy OAuth 2.0 (PKCE).
    etsy_oauth_authorize_url: str = "https://www.etsy.com/oauth/connect"
    etsy_oauth_token_url: str = "https://api.etsy.com/v3/public/oauth/token"
    etsy_redirect_uri: str = "http://localhost:8000/api/auth/etsy/callback"
    etsy_scopes: str = "listings_r listings_w shops_r shops_w"

    # Where to send the browser back to after the OAuth callback completes.
    frontend_url: str = "http://localhost:3000"

    # Stock quantity for made-to-order products. Everything else on a listing --
    # category, price, who_made, when_made, shipping, return policy, production,
    # auto-renew -- comes from the reference listing, never from config (v4 §0/§C).
    default_quantity: int = 999

    # Thumbnail preparation (rank=1 image squared to size).
    # thumbnail_mode: "crop" (centre-crop, no added border — default) or "pad".
    thumbnail_mode: str = "crop"
    thumbnail_padding_pct: int = 8
    thumbnail_size: int = 2000

    # Shop-section auto-assignment. Off by default: never create sections in the
    # user's shop without permission.
    auto_create_sections: bool = False

    # Default content prompt when the taxonomy doesn't say otherwise (apparel shop).
    default_content_template: str = "apparel"

    # LLM provider (Anthropic) for vision analysis + content generation.
    # Key is read from the environment only, never hardcoded (see CLAUDE.md).
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"

    # Object storage root for uploaded originals + processed derivatives.
    storage_dir: str = "./storage"

    # Global daily API budget (app-wide, Personal App = 5.000/day). Tenant budget is per-tenant.
    global_daily_limit: int = 5000
    # New jobs pause once app-wide usage reaches this share of the limit (production-spec C).
    # The rest is held back so jobs already running finish instead of dying at the wall.
    global_pause_percent: int = Field(default=90, ge=1, le=100)

    # Shown in the UI (ToU requires a visible support email).
    support_email: str = "support@example.com"

    # Who operates the service, as named in the Terms and Privacy Policy.
    # Production refuses to start while any is empty, so the published legal
    # pages can never go out naming nobody.
    operator_name: str = ""  # the legal person or business, e.g. "Jane Doe"
    operator_location: str = ""  # e.g. "Istanbul, Türkiye"
    governing_law: str = ""  # e.g. "the Republic of Türkiye"
    dispute_venue: str = ""  # e.g. "the courts of Istanbul"

    # Browser origins allowed to call the API directly (dev). Comma-separated.
    cors_origins: str = "http://localhost:3000"


@lru_cache
def _load_settings() -> Settings:
    """Build Settings from the environment / .env, cached for the process."""
    return Settings()


# Test-only override. When set, ``get_settings()`` returns this instance verbatim,
# bypassing .env *and* the ambient environment so tests never depend on — or reach —
# external configuration (e.g. a real LLM_API_KEY). Production leaves this ``None``.
_override: Settings | None = None


def set_settings_override(settings: Settings | None) -> None:
    """Install an isolated Settings instance (or clear it with ``None``)."""
    global _override
    _override = settings


def clear_settings_cache() -> None:
    """Drop the cached production settings so the next load re-reads the env."""
    _load_settings.cache_clear()


def get_settings() -> Settings:
    """Return the active settings: the test override if set, else the cached load."""
    if _override is not None:
        return _override
    return _load_settings()
