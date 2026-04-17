import asyncio
import time

import pytest

from liquid.cache.memory import InMemoryCache


@pytest.mark.asyncio
class TestInMemoryCache:
    async def test_set_get_roundtrip(self):
        cache = InMemoryCache()
        await cache.set("k1", {"v": 1}, ttl=60)
        result = await cache.get("k1")
        assert result == {"v": 1}

    async def test_missing_key_returns_none(self):
        cache = InMemoryCache()
        result = await cache.get("missing")
        assert result is None

    async def test_delete(self):
        cache = InMemoryCache()
        await cache.set("k1", {"v": 1}, ttl=60)
        await cache.delete("k1")
        assert await cache.get("k1") is None

    async def test_zero_ttl_not_stored(self):
        cache = InMemoryCache()
        await cache.set("k1", {"v": 1}, ttl=0)
        assert await cache.get("k1") is None

    async def test_expiry(self):
        cache = InMemoryCache()
        await cache.set("k1", {"v": 1}, ttl=1)
        # Simulate time passing by manipulating internal state
        cache._data["k1"] = ({"v": 1}, time.monotonic() - 10)
        assert await cache.get("k1") is None

    async def test_concurrent_access(self):
        cache = InMemoryCache()

        async def writer(i):
            await cache.set(f"k{i}", {"v": i}, ttl=60)

        await asyncio.gather(*[writer(i) for i in range(100)])
        for i in range(100):
            assert await cache.get(f"k{i}") == {"v": i}

    async def test_clear(self):
        cache = InMemoryCache()
        await cache.set("k1", {"v": 1}, ttl=60)
        await cache.set("k2", {"v": 2}, ttl=60)
        await cache.clear()
        assert await cache.get("k1") is None
        assert await cache.get("k2") is None
