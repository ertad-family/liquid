import httpx
import pytest

from liquid.cache.memory import InMemoryCache
from liquid.models.schema import Endpoint, EndpointKind
from liquid.sync.fetcher import Fetcher


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        return "token"

    async def delete(self, key):
        pass


def _make_endpoint() -> Endpoint:
    return Endpoint(
        path="/orders",
        method="GET",
        kind=EndpointKind.READ,
    )


@pytest.mark.asyncio
class TestFetcherCache:
    async def test_cache_hit_skips_http(self):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[{"id": 1}])

        transport = httpx.MockTransport(handler)
        cache = InMemoryCache()

        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                cache=cache,
                adapter_id="test-adapter",
                cache_ttl_override={"/orders": 300},
            )
            # First call -> hits API
            r1 = await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://api.example.com",
                auth_ref="vault/test",
            )
            assert call_count == 1
            # Second call -> from cache
            r2 = await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://api.example.com",
                auth_ref="vault/test",
            )
            assert call_count == 1  # Still 1!
            assert r2.records == r1.records
            assert r2.raw_response is None  # Cache hit has no raw_response

    async def test_no_cache_always_hits(self):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            assert call_count == 2

    async def test_cache_control_header_respected(self):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[], headers={"cache-control": "max-age=60"})

        transport = httpx.MockTransport(handler)
        cache = InMemoryCache()
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                cache=cache,
                adapter_id="a",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            assert call_count == 1  # Second hit cached via header

    async def test_no_store_not_cached(self):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[], headers={"cache-control": "no-store"})

        transport = httpx.MockTransport(handler)
        cache = InMemoryCache()
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                cache=cache,
                adapter_id="a",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            assert call_count == 2

    async def test_override_zero_bypasses_cache(self):
        """cache_ttl_override of 0 for an endpoint bypasses cache."""
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[], headers={"cache-control": "max-age=600"})

        transport = httpx.MockTransport(handler)
        cache = InMemoryCache()
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                cache=cache,
                adapter_id="a",
                cache_ttl_override={"/orders": 0},
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            assert call_count == 2

    async def test_override_wins_over_header(self):
        """Per-endpoint override TTL takes precedence over Cache-Control header."""
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[{"id": call_count}], headers={"cache-control": "max-age=1"})

        transport = httpx.MockTransport(handler)
        cache = InMemoryCache()
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                cache=cache,
                adapter_id="a",
                cache_ttl_override={"/orders": 3600},
            )
            r1 = await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            r2 = await fetcher.fetch(
                endpoint=_make_endpoint(),
                base_url="https://a.com",
                auth_ref="v/x",
            )
            assert call_count == 1
            assert r1.records == r2.records
