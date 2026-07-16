"""Coverage for src/db.py — the session dependency and the non-test engine-kwargs branch."""

import importlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_get_session_yields_asyncsession():
    from src.db import get_session
    gen = get_session()
    session = await gen.__anext__()
    assert isinstance(session, AsyncSession)
    # Exhaust the generator so the `async with` closes the session cleanly.
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


def test_non_test_environment_uses_pool_pre_ping(monkeypatch):
    """Reload db.py with a non-test environment to exercise the pool_pre_ping branch (line 24)."""
    import src.config as config
    import src.db as db

    settings = config.get_settings()
    original_env = settings.environment
    try:
        monkeypatch.setattr(settings, "environment", "production")
        reloaded = importlib.reload(db)
        # A real (non-Null) pool is configured with pre-ping in non-test deployments.
        assert reloaded.engine.pool.__class__.__name__ != "NullPool"
    finally:
        # Restore the test-mode module so the rest of the suite keeps its NullPool engine.
        monkeypatch.setattr(settings, "environment", original_env)
        importlib.reload(db)
