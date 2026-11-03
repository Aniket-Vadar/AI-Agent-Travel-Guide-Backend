"""A tiny async TTL cache.

Upstream sources (Wikipedia, Nominatim, Overpass) are rate limited and their data
barely changes, so caching is what keeps the planner fast and polite. The
interface is deliberately Redis-shaped: swap `_MemoryCache` for a Redis client in
a multi-process deployment without touching call sites.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.config import settings

T = TypeVar("T")


class _MemoryCache:
    def __init__(self, max_entries: int = 2048):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._max = max_entries

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires, value = entry
            if expires < time.time():
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        async with self._lock:
            if len(self._data) >= self._max:
                # Drop the entries closest to expiry; cheap and good enough.
                for stale, _ in sorted(self._data.items(), key=lambda kv: kv[1][0])[: self._max // 4]:
                    self._data.pop(stale, None)
            self._data[key] = (time.time() + ttl, value)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            return {"entries": len(self._data)}


cache = _MemoryCache()


def make_key(namespace: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return f"{namespace}:{hashlib.sha1(blob.encode()).hexdigest()[:20]}"


async def cached(
    namespace: str,
    payload: Any,
    producer: Callable[[], Awaitable[T]],
    ttl: int | None = None,
) -> T:
    """Return a cached value or compute, store and return a fresh one."""
    key = make_key(namespace, payload)
    hit = await cache.get(key)
    if hit is not None:
        return hit
    value = await producer()
    if value is not None:
        await cache.set(key, value, ttl or settings.cache_ttl_seconds)
    return value
