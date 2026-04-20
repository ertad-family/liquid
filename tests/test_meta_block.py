"""Tests for the ``_meta`` block builder and response wrapper."""

from __future__ import annotations

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.meta import build_meta, wrap_with_meta
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="[]")


def _make_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="example",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(records, *, include_meta: bool = False) -> tuple[Liquid, httpx.AsyncClient]:
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
        include_meta=include_meta,
    )
    return liquid, client


# ---------------------------------------------------------------------------
# build_meta
# ---------------------------------------------------------------------------


class TestBuildMeta:
    def test_live_defaults(self) -> None:
        meta = build_meta(source="live", adapter="stripe", endpoint="/charges")
        assert meta["source"] == "live"
        assert meta["age_seconds"] == 0
        assert meta["fresh"] is True
        assert meta["truncated"] is False
        assert meta["truncated_at"] is None
        assert meta["adapter"] == "stripe"
        assert meta["endpoint"] == "/charges"
        assert meta["confidence"] == 1.0
        assert "fetched_at" in meta

    def test_cache_with_ttl_decays_confidence(self) -> None:
        meta = build_meta(source="cache", age_seconds=0, ttl_seconds=300)
        assert meta["fresh"] is True
        assert meta["confidence"] == 1.0

        aged = build_meta(source="cache", age_seconds=150, ttl_seconds=300)
        # Half TTL -> confidence ~0.75
        assert 0.7 < aged["confidence"] < 0.8

        expired = build_meta(source="cache", age_seconds=400, ttl_seconds=300)
        assert expired["fresh"] is False
        assert expired["confidence"] == 0.5

    def test_cache_without_ttl(self) -> None:
        meta = build_meta(source="cache", age_seconds=60)
        assert meta["confidence"] == 0.8

    def test_retry_confidence(self) -> None:
        meta = build_meta(source="retry")
        assert meta["confidence"] == 0.9

    def test_truncated_fields(self) -> None:
        meta = build_meta(truncated=True, truncated_at="item_42")
        assert meta["truncated"] is True
        assert meta["truncated_at"] == "item_42"

    def test_extra_merged(self) -> None:
        meta = build_meta(extra={"custom_flag": True})
        assert meta["custom_flag"] is True

    def test_total_count_and_cursor(self) -> None:
        meta = build_meta(total_count=5000, next_cursor="abc")
        assert meta["total_count"] == 5000
        assert meta["next_cursor"] == "abc"


# ---------------------------------------------------------------------------
# wrap_with_meta
# ---------------------------------------------------------------------------


class TestWrapWithMeta:
    def test_wraps_list_with_data_key(self) -> None:
        meta = build_meta()
        wrapped = wrap_with_meta([{"id": 1}, {"id": 2}], meta)
        assert isinstance(wrapped, dict)
        assert wrapped["data"] == [{"id": 1}, {"id": 2}]
        assert wrapped["_meta"]["source"] == "live"

    def test_merges_into_dict(self) -> None:
        meta = build_meta()
        wrapped = wrap_with_meta({"id": 42, "name": "hi"}, meta)
        assert wrapped["id"] == 42
        assert wrapped["name"] == "hi"
        assert wrapped["_meta"]["source"] == "live"

    def test_preserves_existing_meta_key_from_payload(self) -> None:
        meta = build_meta(source="live")
        payload = {"foo": "bar", "_meta": {"source": "user", "custom": True}}
        wrapped = wrap_with_meta(payload, meta)
        # Existing payload _meta wins on source, but non-clashing keys merged in.
        assert wrapped["_meta"]["source"] == "user"
        assert wrapped["_meta"]["custom"] is True
        assert wrapped["_meta"]["confidence"] == 1.0

    def test_scalar_becomes_data_dict(self) -> None:
        wrapped = wrap_with_meta("hello", build_meta())
        assert wrapped["data"] == "hello"
        assert "_meta" in wrapped


# ---------------------------------------------------------------------------
# Liquid.fetch(include_meta=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFetchIncludeMeta:
    async def test_fetch_default_returns_list(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i} for i in range(3)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter)
            assert isinstance(result, list)
            assert len(result) == 3
        finally:
            await client.aclose()

    async def test_fetch_per_call_include_meta(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i} for i in range(3)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter, include_meta=True)
            assert isinstance(result, dict)
            assert result["data"] == records
            assert result["_meta"]["adapter"] == "example"
            assert result["_meta"]["endpoint"] == "/orders"
            assert result["_meta"]["source"] == "live"
            assert result["_meta"]["truncated"] is False
            assert result["_meta"]["returned_items"] == 3
        finally:
            await client.aclose()

    async def test_instance_flag_default_on(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i} for i in range(3)]
        liquid, client = await _make_liquid(records, include_meta=True)
        try:
            result = await liquid.fetch(adapter)
            assert isinstance(result, dict)
            assert result["_meta"]["source"] == "live"
        finally:
            await client.aclose()

    async def test_instance_flag_opt_out_per_call(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i} for i in range(3)]
        liquid, client = await _make_liquid(records, include_meta=True)
        try:
            result = await liquid.fetch(adapter, include_meta=False)
            assert isinstance(result, list)
        finally:
            await client.aclose()

    async def test_include_meta_works_with_empty_response(self) -> None:
        adapter = _make_adapter()
        liquid, client = await _make_liquid([])
        try:
            result = await liquid.fetch(adapter, include_meta=True)
            assert result["data"] == []
            assert result["_meta"]["returned_items"] == 0
            assert result["_meta"]["truncated"] is False
        finally:
            await client.aclose()
