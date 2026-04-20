"""Tests for Liquid.search and Liquid.search_nl — query-based data retrieval."""

import httpx

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        # Fake NL->DSL translation
        return LLMResponse(content='{"status": "paid"}')


def _make_adapter(mappings: list[FieldMapping] | None = None) -> AdapterConfig:
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
        mappings=mappings or [FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(records: list[dict]) -> tuple[Liquid, httpx.AsyncClient]:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=records))
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/x", "test-token")
    liquid = Liquid(
        llm=FakeLLM(),
        vault=vault,
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        http_client=client,
    )
    return liquid, client


class TestSearch:
    async def test_search_filters_records(self):
        records = [
            {"id": 1, "status": "paid", "total": 100},
            {"id": 2, "status": "pending", "total": 50},
            {"id": 3, "status": "paid", "total": 200},
        ]
        adapter = _make_adapter(
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="status", target_field="status"),
                FieldMapping(source_path="total", target_field="total"),
            ]
        )
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.search(adapter, where={"status": "paid"})
            assert resp.meta.returned_items == 2
            assert all(r["status"] == "paid" for r in resp.items)
            assert resp.meta.total_items == 3  # scanned
        finally:
            await client.aclose()

    async def test_search_with_fields(self):
        records = [{"id": 1, "status": "paid", "total": 100, "extra": "x"}]
        adapter = _make_adapter(
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="status", target_field="status"),
                FieldMapping(source_path="total", target_field="total"),
                FieldMapping(source_path="extra", target_field="extra"),
            ]
        )
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.search(
                adapter,
                where={"status": "paid"},
                fields=["id", "total"],
            )
            assert resp.items[0] == {"id": 1, "total": 100}
        finally:
            await client.aclose()

    async def test_search_no_where_fallback(self):
        records = [{"id": i} for i in range(5)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.search(_make_adapter())
            assert resp.meta.returned_items == 5
        finally:
            await client.aclose()

    async def test_search_nl(self):
        records = [
            {"id": 1, "status": "paid"},
            {"id": 2, "status": "pending"},
        ]
        adapter = _make_adapter(
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="status", target_field="status"),
            ]
        )
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.search_nl(adapter, query="paid orders")
            assert len(resp.records) == 1
            assert resp.records[0]["id"] == 1
            assert resp.compiled_query == {"status": "paid"}
            assert resp.query_text == "paid orders"
            assert resp.from_cache is False
        finally:
            await client.aclose()
