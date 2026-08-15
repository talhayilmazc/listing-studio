"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api import health


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(title="Etsy Listing Assistant", version="0.1.0")
    application.include_router(health.router)
    return application


app = create_app()
