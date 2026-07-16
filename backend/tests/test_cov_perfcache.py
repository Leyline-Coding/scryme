"""Coverage for src/perfcache.py — eviction (expired + capacity) and enabled()."""

import pytest
from src import perfcache


@pytest.mark.asyncio
async def test_evict_drops_expired_and_capacity(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "_MAX_ENTRIES", 2)
    now = 1000.0
    # One already-expired entry, one live entry.
    perfcache._store["expired"] = (now - 5, "old")
    perfcache._store["live"] = (now + 100, "fresh")
    perfcache._evict(now)
    assert "expired" not in perfcache._store  # dropped (line: expired sweep)

    # Now fill to capacity with only-live entries, then evict again -> drops nearest-to-expiring.
    perfcache._store.clear()
    perfcache._store["a"] = (now + 10, "a")
    perfcache._store["b"] = (now + 20, "b")
    perfcache._evict(now)  # len >= _MAX_ENTRIES -> pop min expiry ("a")
    assert "a" not in perfcache._store and "b" in perfcache._store


@pytest.mark.asyncio
async def test_memoize_triggers_eviction_when_full(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "enabled", lambda: True)
    monkeypatch.setattr(perfcache, "_MAX_ENTRIES", 2)

    async def factory(v):
        return v

    await perfcache.memoize("k1", lambda: factory(1))
    await perfcache.memoize("k2", lambda: factory(2))
    # Store is at capacity; a third distinct key forces the eviction path before storing.
    await perfcache.memoize("k3", lambda: factory(3))
    assert len(perfcache._store) <= 2
    assert await perfcache.memoize("k3", lambda: factory(99)) == 3  # k3 retained/cached


@pytest.mark.asyncio
async def test_memoize_passthrough_when_disabled(monkeypatch):
    perfcache.clear()
    monkeypatch.setattr(perfcache, "enabled", lambda: False)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    assert await perfcache.memoize("k", factory) == 1
    assert await perfcache.memoize("k", factory) == 2  # recomputed, nothing stored
    assert perfcache._store == {}


def test_enabled_follows_read_only(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert perfcache.enabled() is True
    monkeypatch.setattr(get_settings(), "read_only", False)
    assert perfcache.enabled() is False
