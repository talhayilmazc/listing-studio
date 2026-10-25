"""FastAPI application entrypoint."""

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    accounts,
    personalization_fix,
    admin,
    analytics,
    auth,
    batches,
    board,
    content,
    distribution,
    send,
    profile_shop_table,
    scheduled,
    health,
    imports,
    invite_requests,
    item_options,
    meta,
    mockup_edits,
    mockups,
    pnl,
    profile_links,
    profiles,
    publish,
    schedules,
    shop,
    shop_groups,
    attention,
    shops,
    skus,
    unfinished,
)
from app.core.config import get_settings
from app.core.errortracking import init_error_tracking
from app.core.logsafety import configure_app_logging, install_log_redaction
from app.core.ratelimit import rate_limit
from app.core.security import SecurityMiddleware

# Methods and headers the frontend actually uses. Anything else is refused at
# preflight rather than allowed by wildcard.
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CORS_HEADERS = ["Content-Type"]


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    install_log_redaction()
    configure_app_logging()
    settings = get_settings()
    production = settings.app_env == "production"
    if production:
        _require_legal_identity(settings)
    init_error_tracking("api")

    application = FastAPI(
        title="Etsy Listing Assistant",
        version="0.1.0",
        # The schema is a map of every endpoint and parameter; nobody outside
        # needs it in production.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        dependencies=[Depends(rate_limit)],
    )

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if "*" in origins:
        # A wildcard with credentials would hand every site the session.
        raise RuntimeError("CORS_ORIGINS must list explicit origins, never '*'")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    # JSON and text leave compressed: the API answers the browser itself in production (the
    # tunnel sends /api/* straight here), and everything crosses the server's 14 Mbps uplink.
    # Images, ZIPs and other compressed types are left alone (Starlette's own exclusions).
    application.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    # Added last, so outermost: it must see every request and every response.
    application.add_middleware(SecurityMiddleware)

    application.add_exception_handler(RequestValidationError, _validation_error)
    application.add_exception_handler(FileNotFoundError, _file_missing)

    application.include_router(health.router)
    application.include_router(accounts.router)
    application.include_router(admin.router)
    application.include_router(analytics.router)
    application.include_router(imports.router)
    application.include_router(pnl.router)
    application.include_router(auth.router)
    application.include_router(batches.router)
    application.include_router(board.router)
    application.include_router(content.router)
    application.include_router(item_options.router)
    application.include_router(skus.router)
    application.include_router(invite_requests.router)
    application.include_router(meta.router)
    # Before profiles: its fixed paths (/link-suggestions) must not be read as a profile id.
    application.include_router(profile_links.router)
    application.include_router(profiles.router)
    application.include_router(shop_groups.router)
    application.include_router(attention.router)
    application.include_router(mockups.router)
    application.include_router(mockup_edits.router)
    application.include_router(distribution.router)
    application.include_router(send.router)
    application.include_router(profile_shop_table.router)
    application.include_router(scheduled.router)
    application.include_router(shop.router)
    application.include_router(shops.router)
    application.include_router(publish.router)
    application.include_router(unfinished.router)
    application.include_router(schedules.router)
    application.include_router(personalization_fix.router)
    return application


def _require_legal_identity(settings) -> None:  # noqa: ANN001
    """Refuse to serve production traffic with legal pages that name no one.

    The Terms and Privacy Policy state who operates the service, which law
    governs it, and where to write. Placeholders there are not a cosmetic bug:
    a policy that names no controller is not a policy.
    """
    missing = [
        name
        for name in ("operator_name", "operator_location", "governing_law", "dispute_venue")
        if not getattr(settings, name).strip()
    ]
    if not settings.support_email.strip() or settings.support_email.endswith("@example.com"):
        missing.append("support_email")
    if missing:
        raise RuntimeError(
            "production needs the operator details for the legal pages; set: "
            + ", ".join(m.upper() for m in missing)
        )


async def _file_missing(_: Request, exc: FileNotFoundError) -> JSONResponse:
    """A stored file that is not there (removed by retention, never written, or a bad key):
    404 in plain words, not a 500. The path is not echoed."""
    logger.info("stored file missing: %s", type(exc).__name__)
    return JSONResponse(status_code=404, content={"detail": "This file is no longer stored."})


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without echoing the submitted values back.

    FastAPI's default includes each offending ``input``, which for a malformed
    login or registration is the whole body — password included. Location and
    message are all a client needs to fix the request.
    """
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


app = create_app()
