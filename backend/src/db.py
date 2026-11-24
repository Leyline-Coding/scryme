"""Async SQLAlchemy engine, session factory, and declarative base."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from src.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

# Under pytest each test runs on its own event loop; NullPool avoids reusing an asyncpg
# connection across loops (which raises "attached to a different loop").
_engine_kwargs: dict = {"echo": _settings.debug}
if _settings.environment == "test":
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session.

    Request sessions get a statement timeout, scoped to the request's transaction so it never
    leaks onto a pooled connection that a background job picks up next.
    """
    async with SessionLocal() as session:
        timeout = get_settings().statement_timeout_ms
        if timeout > 0:
            await session.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(timeout)}
            )
        yield session
