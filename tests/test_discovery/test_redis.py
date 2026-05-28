"""Redis discovery: namespace grouping (deterministic) + a live end-to-end run."""

from __future__ import annotations

import os

import pytest

from liquid.discovery.redis import RedisDiscovery, _keys_to_endpoints, _service_name


def test_keys_to_endpoints_groups_by_prefix():
    endpoints = _keys_to_endpoints(["user:1", "user:2", "session:a", "plainkey"])
    by_path = {ep.path: ep for ep in endpoints}
    assert set(by_path) == {"/user", "/session", "/keys"}
    assert by_path["/user"].transport_meta == {"kind": "namespace", "prefix": "user"}
    assert by_path["/keys"].transport_meta["prefix"] == ""
    assert all(ep.protocol == "redis" for ep in endpoints)


def test_service_name():
    assert _service_name("redis://localhost:6379/0") == "redis-localhost"


async def test_non_redis_url_returns_none():
    assert await RedisDiscovery().discover("https://example.com") is None


_REDIS_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")


@pytest.mark.network
async def test_live_redis_discovery_and_fetch():
    pytest.importorskip("redis")
    import redis.asyncio as redis_async

    from liquid.exceptions import VaultError
    from liquid.sync.fetcher import Fetcher

    class FakeVault:
        async def get(self, key):
            raise VaultError(key)

        async def store(self, key, value): ...
        async def delete(self, key): ...

    client = redis_async.from_url(_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        pytest.skip(f"Redis unreachable: {e}")

    await client.set("liqtest:str", "hello")
    await client.hset("liqtest:hash", mapping={"a": "1", "b": "2"})
    await client.rpush("liqtest:list", "x", "y", "z")

    try:
        schema = await RedisDiscovery().discover(_REDIS_URL)
        assert schema is not None
        assert schema.discovery_method == "redis"
        ns = next(ep for ep in schema.endpoints if ep.transport_meta["prefix"] == "liqtest")

        import httpx

        async with httpx.AsyncClient() as http_client:
            fetcher = Fetcher(http_client=http_client, vault=FakeVault())
            page = await fetcher.fetch(endpoint=ns, base_url=_REDIS_URL, auth_ref="none", extra_params={"limit": 100})
        by_key = {r["key"]: r for r in page.records}
        assert by_key["liqtest:str"]["type"] == "string"
        assert by_key["liqtest:str"]["value"] == "hello"
        assert by_key["liqtest:hash"]["type"] == "hash"
        assert by_key["liqtest:hash"]["value"] == {"a": "1", "b": "2"}
        assert by_key["liqtest:list"]["value"] == ["x", "y", "z"]
    finally:
        for key in await client.keys("liqtest:*"):
            await client.delete(key)
        await client.aclose()
