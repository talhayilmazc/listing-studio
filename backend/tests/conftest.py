"""Test fixtures.

Models are exercised against an in-memory SQLite database with foreign-key
enforcement enabled. The portable column types in ``app.db.models`` allow the
schema to be created here without a Postgres server; the Alembic migration is
what runs against Postgres in real environments.
"""

from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, set_settings_override
from app.db import models  # noqa: F401  (registers models on Base.metadata)
from app.db.base import Base


@pytest.fixture()
def test_settings() -> Settings:
    """Isolated settings for a test.

    Built with ``_env_file=None`` so the real ``.env`` is never read, and with the
    safety-critical fields pinned as explicit kwargs (init values outrank any
    ambient environment variable in pydantic-settings). In particular
    ``llm_api_key=""`` guarantees no test can reach a real provider, and the
    quota/limit values are fixed here rather than inherited from whatever ``.env``
    happens to contain. Tests may mutate the returned instance before use.
    """
    return Settings(
        _env_file=None,
        app_env="test",
        llm_api_key="",  # no provider is reachable from tests
        global_daily_limit=5000,
        global_pause_percent=90,
        etsy_client_id="",
        etsy_client_secret="",
        # The legal-identity fields decide whether production starts; tests set them explicitly.
        support_email="support@example.com",
        operator_name="",
        operator_location="",
        governing_law="",
        dispute_venue="",
    )


@pytest.fixture(autouse=True)
def _isolate_settings(test_settings: Settings) -> Iterator[None]:
    """Install the isolated settings for every test, then clear the override."""
    set_settings_override(test_settings)
    try:
        yield
    finally:
        set_settings_override(None)
        # The app caches one Redis client per process. Each test runs on its own
        # event loop, so a client left over from the previous test is unusable.
        from app.api.deps import get_redis

        get_redis.cache_clear()


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn: object, _: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine)
    with factory() as sess:
        yield sess


@pytest_asyncio.fixture()
async def async_sm() -> AsyncIterator[async_sessionmaker]:
    """Async in-memory SQLite session factory with FK enforcement."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn: object, _: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def fake_redis() -> AsyncIterator[FakeAsyncRedis]:
    """Isolated in-process fake Redis (supports Lua EVAL via lupa)."""
    client = FakeAsyncRedis()
    try:
        yield client
    finally:
        await client.flushall()
        await client.aclose()


@pytest.fixture()
def make_image() -> "Callable[..., bytes]":
    """Factory producing in-memory image bytes (no binary fixtures on disk)."""
    import io

    from PIL import Image

    def _make(
        width: int = 1200,
        height: int = 800,
        color: tuple[int, int, int] = (200, 30, 30),
        fmt: str = "PNG",
    ) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), color).save(buffer, format=fmt)
        return buffer.getvalue()

    return _make
