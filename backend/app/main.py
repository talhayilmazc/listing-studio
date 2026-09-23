"""FastAPI application entrypoint."""

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import accounts, admin, auth, batches, content, health, meta, profiles, publish, shop
from app.core.config import get_settings
from app.core.errortracking import init_error_tracking
from app.core.logsafety import install_log_redaction
from app.core.ratelimit import rate_limit
from app.core.security import SecurityMiddleware

# Methods and headers the frontend actually uses. Anything else is refused at
# preflight rather than allowed by wildcard.
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CORS_HEADERS = ["Content-Type"]


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    install_log_redaction()
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
    # Added last, so outermost: it must see every request and every response.
    application.add_middleware(SecurityMiddleware)

    application.add_exception_handler(RequestValidationError, _validation_error)

    application.include_router(health.router)
    application.include_router(accounts.router)
    application.include_router(admin.router)
    application.include_router(auth.router)
    application.include_router(batches.router)
    application.include_router(content.router)
    application.include_router(meta.router)
    application.include_router(profiles.router)
    application.include_router(shop.router)
    application.include_router(publish.router)
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
