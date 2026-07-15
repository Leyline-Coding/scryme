"""The read-only TTL cache (perfcache) and static Cache-Control headers."""

import pytest
from src import perfcache


@pytest.mark.asyncio
async def test_memoize_caches_when_enabled(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "enabled", lambda: True)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    assert await perfcache.memoize("k", factory) == 1
    assert await perfcache.memoize("k", factory) == 1  # served from cache
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_memoize_passthrough_when_disabled(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "enabled", lambda: False)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    assert await perfcache.memoize("k", factory) == 1
    assert await perfcache.memoize("k", factory) == 2  # recomputed every call
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_memoize_expires_after_ttl(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "enabled", lambda: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(perfcache.time, "monotonic", lambda: clock["t"])
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    await perfcache.memoize("k", factory, ttl=10)
    clock["t"] = 1005            # still fresh
    await perfcache.memoize("k", factory, ttl=10)
    assert calls["n"] == 1
    clock["t"] = 1011            # past ttl
    await perfcache.memoize("k", factory, ttl=10)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_static_assets_get_cache_control(client):
    r = await client.get("/static/app.css")
    assert r.status_code == 200
    assert "max-age=86400" in r.headers.get("cache-control", "")
