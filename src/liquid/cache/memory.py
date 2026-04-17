"""In-memory cache implementation."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class InMemoryCache:
    """Simple in-memory cache with TTL.

    Default implementation. Safe for single-process use.
    For multi-process deployments, use RedisCache.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        if ttl <= 0:
            return
        async with self._lock:
            expires_at = time.monotonic() + ttl
            self._data[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def clear(self) -> None:
        """Non-protocol method for testing."""
        async with self._lock:
            self._data.clear()
