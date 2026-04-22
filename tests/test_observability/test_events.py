"""Unit tests for the retrospective event store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from liquid.observability import EventKind, FetchEvent, InMemoryEventStore


class TestInMemoryEventStore:
    async def test_append_and_query_all(self) -> None:
        store = InMemoryEventStore()
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="stripe", endpoint="/customers"))
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="stripe", endpoint="/charges"))
        events = await store.query()
        assert len(events) == 2
        # Newest first.
        assert events[0].endpoint == "/charges"

    async def test_query_by_adapter(self) -> None:
        store = InMemoryEventStore()
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="stripe", endpoint="/a"))
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="shopify", endpoint="/b"))
        stripe_only = await store.query(adapter="stripe")
        assert len(stripe_only) == 1
        assert stripe_only[0].endpoint == "/a"

    async def test_query_by_endpoint(self) -> None:
        store = InMemoryEventStore()
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a"))
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/b"))
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a"))
        a_only = await store.query(endpoint="/a")
        assert len(a_only) == 2

    async def test_query_since_until(self) -> None:
        store = InMemoryEventStore()
        base = datetime.now(UTC)
        old = FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a", occurred_at=base - timedelta(hours=2))
        recent = FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a", occurred_at=base - timedelta(minutes=10))
        await store.append(old)
        await store.append(recent)

        last_hour = await store.query(since=base - timedelta(hours=1))
        assert len(last_hour) == 1
        assert last_hour[0] is recent

    async def test_query_errors_only(self) -> None:
        store = InMemoryEventStore()
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a", status_code=200))
        await store.append(
            FetchEvent(
                kind=EventKind.FETCH,
                adapter="x",
                endpoint="/b",
                status_code=500,
                error_type="ServiceDownError",
                error_message="boom",
            )
        )
        errs = await store.query(errors_only=True)
        assert len(errs) == 1
        assert errs[0].error_type == "ServiceDownError"

    async def test_query_by_kind(self) -> None:
        store = InMemoryEventStore()
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint="/a"))
        await store.append(FetchEvent(kind=EventKind.STREAM, adapter="x", endpoint="/s"))
        streams = await store.query(kind=EventKind.STREAM)
        assert len(streams) == 1
        assert streams[0].endpoint == "/s"
        # Accept plain string for convenience.
        by_string = await store.query(kind="fetch")
        assert len(by_string) == 1

    async def test_ring_buffer_cap(self) -> None:
        store = InMemoryEventStore(max_events=5)
        for i in range(10):
            await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint=f"/{i}"))
        assert len(store) == 5
        events = await store.query()
        endpoints = [e.endpoint for e in events]
        assert endpoints == ["/9", "/8", "/7", "/6", "/5"]

    async def test_query_limit(self) -> None:
        store = InMemoryEventStore()
        for i in range(10):
            await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint=f"/{i}"))
        events = await store.query(limit=3)
        assert len(events) == 3
        assert events[0].endpoint == "/9"


class TestLiquidIntegration:
    async def test_fetch_records_event(self) -> None:
        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k, v): ...
            async def get(self, k):
                return "tok"

            async def delete(self, k): ...

        class FakeSink:
            async def deliver(self, records):
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):
                raise NotImplementedError

        def handler(request):
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        schema = APISchema(
            source_url="https://api.example",
            service_name="example-svc",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/items", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/items"]))
        store = InMemoryEventStore()
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            event_store=store,
        )
        await liquid.fetch(config, "/items")
        await client.aclose()

        events = await store.query()
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == EventKind.FETCH
        assert ev.adapter == "example-svc"
        assert ev.endpoint == "/items"
        assert ev.method == "GET"
        assert ev.status_code == 200
        assert ev.record_count == 2
        assert ev.duration_ms >= 0

    async def test_buggy_store_does_not_break_fetch(self) -> None:
        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k, v): ...
            async def get(self, k):
                return "tok"

            async def delete(self, k): ...

        class FakeSink:
            async def deliver(self, records):
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):
                raise NotImplementedError

        class BrokenStore:
            async def append(self, event):  # type: ignore[no-untyped-def]
                raise RuntimeError("db down")

            async def query(self, **kwargs):  # type: ignore[no-untyped-def]
                return []

        def handler(request):
            return httpx.Response(200, json=[{"id": 1}])

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/x", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/x"]))
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            event_store=BrokenStore(),
        )
        # Fetch must complete despite the broken store.
        result = await liquid.fetch(config, "/x")
        await client.aclose()
        assert isinstance(result, list)


@pytest.mark.parametrize("count", [0, 1, 5])
async def test_len_matches_appended(count: int) -> None:
    store = InMemoryEventStore()
    for i in range(count):
        await store.append(FetchEvent(kind=EventKind.FETCH, adapter="x", endpoint=f"/{i}"))
    assert len(store) == count
