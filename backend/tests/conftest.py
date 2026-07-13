"""Pytest fixtures.

Tests run against a real PostgreSQL instance (CI provides one; locally use the dev compose DB
or set SCRYME_DATABASE_URL). The schema is created from ORM metadata before the suite and
dropped afterward, so tests never depend on migration ordering.

Force test mode *before* importing the app so the engine is built with NullPool (see src/db.py).
"""

import asyncio
import os
import tempfile

os.environ.setdefault("SCRYME_ENVIRONMENT", "test")
# Point data/image dirs at a writable temp location (the default /data is root-owned and not
# writable on CI runners).
_tmp_data = os.path.join(tempfile.gettempdir(), "scryme-test")
os.environ.setdefault("SCRYME_DATA_DIR", _tmp_data)
os.environ.setdefault("SCRYME_IMAGE_CACHE_DIR", os.path.join(_tmp_data, "images"))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import src.models  # noqa: E402,F401  register all models on Base.metadata before create_all
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from src.db import Base, SessionLocal, engine  # noqa: E402
from src.main import app  # noqa: E402


async def _create_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _drop_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # Plain (sync) fixture using asyncio.run avoids cross-event-loop fixture scoping issues.
    asyncio.run(_create_schema())
    yield
    asyncio.run(_drop_schema())


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    """Truncate mutable tables between tests for isolation."""
    yield
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE collection_card RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE saved_search RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE deck RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE checklist RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE price_snapshot RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE price_target RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE import_snapshot RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE wishlist RESTART IDENTITY CASCADE"))
        await conn.execute(text("TRUNCATE card_embedding CASCADE"))
        await conn.execute(text("TRUNCATE cards CASCADE"))


@pytest_asyncio.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
