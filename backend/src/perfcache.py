"""Tiny in-process TTL cache for expensive, read-only-safe computations.

Only active when the deployment is read-only (e.g. the public demo). There the collection
never changes, so recomputing facets / stats / the value chart on every request is pure waste —
memoizing them for a few minutes cuts most of the per-request database work. A normal self-host
mutates its collection, so the cache stays a transparent no-op there to avoid serving stale numbers.

Cache values must be *detached-safe*: plain data or rendered output (dataclasses, strings, SVG),
never live ORM instances (which would break on lazy attribute access after their session closes).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar

from src.config import get_settings

_T = TypeVar("_T")

_DEFAULT_TTL = 300.0  # seconds
_MAX_ENTRIES = 512    # bound memory even if many distinct queries are cached

_store: dict[Hashable, tuple[float, object]] = {}


def enabled() -> bool:
    """Caching is on only for read-only deployments (the demo)."""
    return get_settings().read_only


def _evict(now: float) -> None:
    # Drop expired entries first; if still full, drop the entry nearest to expiring.
    for key in [k for k, (exp, _) in _store.items() if exp <= now]:
        _store.pop(key, None)
    if len(_store) >= _MAX_ENTRIES:
        _store.pop(min(_store, key=lambda k: _store[k][0]), None)


async def memoize(
    key: Hashable, factory: Callable[[], Awaitable[_T]], ttl: float = _DEFAULT_TTL
) -> _T:
    """Return the cached value for ``key``, else compute it via ``factory`` and store it.

    When not read-only this is a pass-through (``factory`` runs every call, nothing is stored).
    """
    if not enabled():
        return await factory()
    now = time.monotonic()
    hit = _store.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]  # type: ignore[return-value]
    value = await factory()
    if len(_store) >= _MAX_ENTRIES:
        _evict(now)
    _store[key] = (now + ttl, value)
    return value


def clear() -> None:
    """Drop everything (used by tests)."""
    _store.clear()
