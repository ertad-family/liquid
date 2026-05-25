"""The no-LLM runtime path: discover once, then fetch forever with llm=None.

AI participates only at setup (discovery + mapping). Once an AdapterConfig
exists, fetching is pure deterministic HTTP + transforms — no LLM backend
required, no per-call provider cost. These tests prove a Liquid built with
``llm=None`` fetches and maps correctly, including across a JSON round-trip of
the persisted config (the realistic "save the adapter, reload it later" flow).
"""

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

pytestmark = pytest.mark.asyncio


def _make_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="total", target_field="total_cents"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_no_llm_liquid(records):
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=records))
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/x", "test-token")
    liquid = Liquid(llm=None, vault=vault, sink=CollectorSink(), http_client=client)
    return liquid, client


async def test_fetch_works_without_llm():
    liquid, client = await _make_no_llm_liquid([{"id": 1, "total": 9999}, {"id": 2, "total": 100}])
    try:
        data = await liquid.fetch(_make_adapter())
    finally:
        await client.aclose()
    assert data == [{"id": 1, "total_cents": 9999}, {"id": 2, "total_cents": 100}]


async def test_fetch_after_json_round_trip_without_llm():
    """Persist the adapter to JSON, reload it, fetch with no LLM — the
    production 'discover once, sync forever' loop."""
    original = _make_adapter()
    blob = original.model_dump(by_alias=True, mode="json")
    reloaded = AdapterConfig.model_validate(blob)

    liquid, client = await _make_no_llm_liquid([{"id": 7, "total": 4200}])
    try:
        data = await liquid.fetch(reloaded)
    finally:
        await client.aclose()
    assert data == [{"id": 7, "total_cents": 4200}]


async def test_identity_self_heal_without_llm():
    """A stale path is dropped and an identity match recovered — no LLM needed."""
    adapter = _make_adapter()
    adapter.mappings = [
        FieldMapping(source_path="nonexistent.path", target_field="id"),
        FieldMapping(source_path="total", target_field="total_cents"),
    ]
    liquid, client = await _make_no_llm_liquid([{"id": 5, "total": 1}])
    try:
        data = await liquid.fetch(adapter)  # auto_repair on, llm=None
    finally:
        await client.aclose()
    # identity fallback recovers `id` from the live record without an LLM call
    assert data == [{"id": 5, "total_cents": 1}]
