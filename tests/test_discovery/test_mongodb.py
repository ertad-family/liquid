"""MongoDB discovery: endpoint shape (deterministic) + a live end-to-end run."""

from __future__ import annotations

import os

import pytest

from liquid.discovery.mongodb import MongoDBDiscovery, _collection_endpoint


def test_collection_endpoint_shape():
    ep = _collection_endpoint("shop", "orders", ["_id", "total"])
    assert ep.path == "/orders"
    assert ep.protocol == "mongodb"
    assert ep.transport_meta == {
        "kind": "collection",
        "database": "shop",
        "collection": "orders",
        "fields": ["_id", "total"],
    }
    assert set(ep.response_schema["properties"]) == {"_id", "total"}


async def test_non_mongo_url_returns_none():
    assert await MongoDBDiscovery().discover("https://example.com") is None
    assert await MongoDBDiscovery().discover("postgres://h/db") is None


_MONGO_URL = os.environ.get("MONGO_TEST_URL", "mongodb://localhost:27017/liquid_test")


@pytest.mark.network
async def test_live_mongodb_discovery_and_fetch():
    pytest.importorskip("pymongo")
    from pymongo import AsyncMongoClient

    from liquid.exceptions import VaultError
    from liquid.sync.fetcher import Fetcher

    class FakeVault:
        async def get(self, key):
            raise VaultError(key)

        async def store(self, key, value): ...
        async def delete(self, key): ...

    admin = AsyncMongoClient(_MONGO_URL, serverSelectionTimeoutMS=2000)
    try:
        await admin.admin.command("ping")
    except Exception as e:
        await admin.close()
        pytest.skip(f"MongoDB unreachable: {e}")

    db_name = _MONGO_URL.rsplit("/", 1)[-1].split("?", 1)[0]
    coll = admin[db_name]["people"]
    await coll.delete_many({})
    await coll.insert_many([{"name": "alice", "age": 30}, {"name": "bob", "age": 25}])

    try:
        schema = await MongoDBDiscovery().discover(_MONGO_URL)
        assert schema is not None
        assert schema.discovery_method == "mongodb"
        people = next(ep for ep in schema.endpoints if ep.transport_meta["collection"] == "people")
        assert "name" in people.transport_meta["fields"]

        import httpx

        async with httpx.AsyncClient() as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            page = await fetcher.fetch(
                endpoint=people, base_url=_MONGO_URL, auth_ref="none", extra_params={"limit": 10}
            )
            assert {r["name"] for r in page.records} == {"alice", "bob"}
            # ObjectId was coerced to a string
            assert isinstance(page.records[0]["_id"], str)

            filtered = await fetcher.fetch(
                endpoint=people, base_url=_MONGO_URL, auth_ref="none", extra_params={"name": "bob"}
            )
            assert [r["name"] for r in filtered.records] == ["bob"]
    finally:
        await coll.drop()
        await admin.close()
