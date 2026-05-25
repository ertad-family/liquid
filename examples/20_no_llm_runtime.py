"""No-LLM runtime — discover once, sync forever without a model.

Liquid uses AI only at *setup* (discovery + field mapping). Once you have an
``AdapterConfig`` you can persist it and run the deterministic runtime with
``llm=None``: pure HTTP + transforms, no per-call provider cost, reproducible.

This script is self-contained and offline — it fakes the upstream API with an
httpx MockTransport so you can run it with no keys and no network:

    python examples/20_no_llm_runtime.py
"""

from __future__ import annotations

import asyncio
import json

import httpx

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

# --- Setup-time artifact (normally produced by liquid.get_or_create + an LLM) ---
# In production you'd run get_or_create once with a model, then save this dict.
ADAPTER = AdapterConfig(
    schema=APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
        auth=AuthRequirement(type="bearer", tier="A"),
    ),
    auth_ref="vault/example",
    mappings=[
        FieldMapping(source_path="id", target_field="id"),
        FieldMapping(source_path="total", target_field="total_cents"),
        FieldMapping(source_path="customer.email", target_field="customer_email"),
    ],
    sync=SyncConfig(endpoints=["/orders"]),
)

UPSTREAM_ROWS = [
    {"id": 1, "total": 9999, "customer": {"email": "vip@co.com"}},
    {"id": 2, "total": 150, "customer": {"email": "bob@co.com"}},
]


async def main() -> None:
    # 1. Persist the adapter the way a real deployment would (JSON on disk / DB).
    blob = ADAPTER.model_dump(by_alias=True, mode="json")
    print("=== persisted adapter (setup output) ===")
    print(json.dumps(blob["mappings"], indent=2), "\n")

    # 2. Later run: reload it and build Liquid with NO LLM at all.
    reloaded = AdapterConfig.model_validate(blob)

    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=UPSTREAM_ROWS))
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/example", "tok_live_xxx")

    liquid = Liquid(llm=None, vault=vault, sink=CollectorSink(), http_client=client)
    try:
        data = await liquid.fetch(reloaded, "/orders")
    finally:
        await client.aclose()

    print("=== fetch() with llm=None — zero model calls ===")
    for row in data:
        print(" ", row)

    assert data == [
        {"id": 1, "total_cents": 9999, "customer_email": "vip@co.com"},
        {"id": 2, "total_cents": 150, "customer_email": "bob@co.com"},
    ]
    print("\nMapped + nested-path-extracted deterministically, no LLM in the loop.")


if __name__ == "__main__":
    asyncio.run(main())
