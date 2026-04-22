"""Idempotency store — in-memory default, overridable for production."""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class IdempotencyStore(Protocol):
    """Deduplicates webhook event IDs.

    For production, back this with Redis or a TTL-indexed table. The
    in-memory default is process-local and capped by ``max_size``.
    """

    async def seen(self, event_id: str) -> bool: ...
    async def mark(self, event_id: str, ttl_seconds: int = 86400) -> None: ...


class InMemoryIdempotencyStore:
    """LRU-capped in-memory store with per-entry TTL.

    Thread-safe for single-event-loop use (no locking — relies on the
    asyncio cooperative model).
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._max_size = max_size
        self._data: dict[str, float] = {}

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, exp in self._data.items() if exp <= now]
        for k in expired:
            del self._data[k]

    def _evict_oldest_if_full(self) -> None:
        if len(self._data) < self._max_size:
            return
        # drop the entry with the earliest expiry
        oldest = min(self._data, key=lambda k: self._data[k])
        del self._data[oldest]

    async def seen(self, event_id: str) -> bool:
        self._evict_expired()
        return event_id in self._data

    async def mark(self, event_id: str, ttl_seconds: int = 86400) -> None:
        self._evict_expired()
        self._evict_oldest_if_full()
        self._data[event_id] = time.time() + ttl_seconds
