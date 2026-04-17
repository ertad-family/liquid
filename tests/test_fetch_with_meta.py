"""Tests for Liquid.fetch_with_meta — context-window-aware fetch."""

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import (
    CollectorSink,
    InMemoryAdapterRegistry,
    InMemoryVault,
)
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="[]")


def _make_adapter(mappings: list[FieldMapping] | None = None) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(path="/orders", method="GET", kind=EndpointKind.READ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=mappings
        or [
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="name", target_field="name"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(records):
    def handler(_req):
        return httpx.Response(200, json=records)

    transport = httpx.MockTransport(handler)
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


@pytest.mark.asyncio
class TestFetchWithMeta:
    async def test_fetch_with_meta_basic(self):
        adapter = _make_adapter()
        records = [{"id": i, "name": f"n{i}"} for i in range(10)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter)
            assert resp.meta.returned_items == 10
            assert resp.meta.total_items == 10
            assert not resp.meta.truncated
            assert resp.meta.estimated_tokens > 0
        finally:
            await client.aclose()

    async def test_head_truncates(self):
        adapter = _make_adapter()
        records = [{"id": i, "name": f"n{i}"} for i in range(100)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, head=5)
            assert resp.meta.returned_items == 5
            assert resp.meta.total_items == 100
            assert resp.meta.truncated
            assert len(resp.items) == 5
        finally:
            await client.aclose()

    async def test_limit_truncates(self):
        adapter = _make_adapter()
        records = [{"id": i, "name": f"n{i}"} for i in range(100)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, limit=10)
            assert resp.meta.returned_items == 10
            assert resp.meta.truncated
        finally:
            await client.aclose()

    async def test_tail_returns_last_n(self):
        adapter = _make_adapter()
        records = [{"id": i, "name": f"n{i}"} for i in range(20)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, tail=3)
            assert resp.meta.returned_items == 3
            assert resp.items[0]["id"] == 17
            assert resp.items[-1]["id"] == 19
            assert resp.meta.truncated
        finally:
            await client.aclose()

    async def test_fields_selection(self):
        # Include "extra" via an extra mapping so it survives record-mapping,
        # then prove fetch_with_meta drops it via fields= selection.
        adapter = _make_adapter(
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="name", target_field="name"),
                FieldMapping(source_path="extra", target_field="extra"),
            ]
        )
        records = [{"id": 1, "name": "a", "extra": "x"}]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, fields=["id", "name"])
            assert resp.items[0] == {"id": 1, "name": "a"}
            assert "extra" not in resp.items[0]
        finally:
            await client.aclose()

    async def test_summary_mode(self):
        adapter = _make_adapter(
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="price", target_field="price"),
            ]
        )
        records = [{"id": i, "price": i * 10} for i in range(5)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, summary=True)
            assert resp.summary is not None
            assert resp.summary["count"] == 5
            assert resp.summary["price"]["sum"] == 100
            assert len(resp.items) == 0
        finally:
            await client.aclose()

    async def test_max_tokens_truncates(self):
        adapter = _make_adapter()
        records = [{"id": i, "name": "x" * 500} for i in range(20)]
        liquid, client = await _make_liquid(records)
        try:
            resp = await liquid.fetch_with_meta(adapter, max_tokens=200)
            assert resp.meta.truncated
            assert resp.meta.returned_items < 20
        finally:
            await client.aclose()

    async def test_backward_compat_plain_fetch(self):
        """Old fetch() still returns list[dict]."""
        adapter = _make_adapter()
        records = [{"id": i, "name": f"n{i}"} for i in range(3)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter)
            assert isinstance(result, list)
            assert len(result) == 3
        finally:
            await client.aclose()
