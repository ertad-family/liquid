"""0.24 Retrospective observability — "what did the agent do last hour?"

Plug an :class:`EventStore` into :class:`Liquid` and every fetch/stream is
recorded with timing, status, record count, and the signals it raised.
Query the store by adapter, endpoint, time window, or error status —
drops right into any post-hoc debugging workflow, no OTEL required.

Default :class:`InMemoryEventStore` is a ring buffer; swap for Redis /
Postgres by implementing the :class:`EventStore` protocol.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from liquid import (
    AdapterConfig,
    APISchema,
    AuthRequirement,
    Endpoint,
    InMemoryEventStore,
    Liquid,
    SyncConfig,
)
from liquid.exceptions import VaultError


class InMemoryVault:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class NullSink:
    async def deliver(self, records):  # type: ignore[no-untyped-def]
        return None


class NullLLM:
    async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/broken":
        return httpx.Response(500, json={"error": "internal"})
    return httpx.Response(200, json=[{"id": 1}, {"id": 2}])


async def main() -> None:
    vault = InMemoryVault({"r": "tok"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = InMemoryEventStore()

    schema = APISchema(
        source_url="https://api.example",
        service_name="shop",
        discovery_method="openapi",
        endpoints=[
            Endpoint(path="/orders", method="GET"),
            Endpoint(path="/customers", method="GET"),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    config_orders = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/orders"]))
    config_customers = AdapterConfig(
        schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/customers"])
    )

    liquid = Liquid(
        llm=NullLLM(),
        vault=vault,
        sink=NullSink(),
        http_client=client,
        event_store=store,
    )

    # Simulate an agent burst of calls
    for _ in range(3):
        await liquid.fetch(config_orders, "/orders")
    for _ in range(2):
        await liquid.fetch(config_customers, "/customers")

    await client.aclose()

    print(f"=== Event store recorded {len(store)} fetches ===")

    print("\n--- Last hour, all calls ---")
    recent = await store.query(since=datetime.now(UTC) - timedelta(hours=1))
    for ev in recent:
        print(
            f"  [{ev.occurred_at:%H:%M:%S}] {ev.method} {ev.adapter}{ev.endpoint} "
            f"→ {ev.status_code} ({ev.duration_ms}ms, {ev.record_count} records)"
        )

    print("\n--- Only /orders ---")
    orders = await store.query(endpoint="/orders")
    print(f"  {len(orders)} events")

    print("\n--- Errors only (none this run) ---")
    errs = await store.query(errors_only=True)
    print(f"  {len(errs)} error events")


if __name__ == "__main__":
    asyncio.run(main())
