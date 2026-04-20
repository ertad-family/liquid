"""Tests for ``max_tokens`` truncation on fetch / execute responses."""

from __future__ import annotations

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.truncate import (
    MAX_UNTRUNCATED_STR_CHARS,
    apply_max_tokens,
    estimate_tokens,
)


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
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="name", target_field="name"),
        ],
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
# Unit tests: apply_max_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_returns_small_number(self) -> None:
        # json.dumps([]) is "[]" -> 2 chars -> 0 tokens.
        assert estimate_tokens([]) == 0

    def test_non_serializable_returns_zero(self) -> None:
        class X:
            pass

        # default=str should let it through, so object is stringified.
        assert estimate_tokens(X()) >= 0


class TestApplyMaxTokensOnList:
    def test_within_budget_passthrough(self) -> None:
        records = [{"id": i} for i in range(5)]
        result = apply_max_tokens(records, 10_000)
        assert result.payload == records
        assert result.truncated is False
        assert result.truncated_at is None

    def test_over_budget_keeps_prefix(self) -> None:
        records = [{"id": i, "body": "x" * 1000} for i in range(20)]
        result = apply_max_tokens(records, 500)
        assert result.truncated is True
        assert len(result.payload) < 20
        assert result.truncated_at is not None
        assert result.truncated_at.startswith("item_")

    def test_empty_list_is_noop(self) -> None:
        result = apply_max_tokens([], 100)
        assert result.payload == []
        assert result.truncated is False

    def test_single_oversize_item_kept(self) -> None:
        # If even the first item exceeds the budget, we still keep one item
        # rather than returning nothing — the agent gets *something* it can
        # decide to drop.
        records = [{"body": "x" * 10_000}]
        result = apply_max_tokens(records, 50)
        assert result.truncated is True
        assert len(result.payload) == 1

    def test_none_max_tokens_passthrough(self) -> None:
        records = [{"id": i} for i in range(5)]
        result = apply_max_tokens(records, None)
        assert result.payload is records
        assert result.truncated is False

    def test_zero_max_tokens_passthrough(self) -> None:
        records = [{"id": i} for i in range(5)]
        result = apply_max_tokens(records, 0)
        assert result.payload is records
        assert result.truncated is False


class TestApplyMaxTokensOnDict:
    def test_dict_within_budget(self) -> None:
        payload = {"id": 1, "name": "short"}
        result = apply_max_tokens(payload, 1_000)
        assert result.payload == payload
        assert result.truncated is False

    def test_dict_string_field_truncated(self) -> None:
        payload = {"id": 1, "body": "x" * (MAX_UNTRUNCATED_STR_CHARS + 200)}
        result = apply_max_tokens(payload, 50)
        assert result.truncated is True
        assert result.payload["id"] == 1
        assert result.payload["body"] == "...[truncated]"
        assert result.truncated_at is not None
        assert result.truncated_at.startswith("field:")

    def test_dict_multiple_long_strings(self) -> None:
        payload = {
            "a": "x" * 2000,
            "b": "y" * 2000,
            "c": "short",
        }
        result = apply_max_tokens(payload, 20)
        assert result.truncated is True
        # Both long strings replaced.
        assert result.payload["a"] == "...[truncated]"
        assert result.payload["b"] == "...[truncated]"
        # Short field untouched.
        assert result.payload["c"] == "short"


class TestApplyMaxTokensOnScalar:
    def test_scalar_passthrough(self) -> None:
        result = apply_max_tokens("hello", 100)
        assert result.payload == "hello"
        assert result.truncated is False


# ---------------------------------------------------------------------------
# Liquid.fetch(max_tokens=...) integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFetchMaxTokensIntegration:
    async def test_fetch_with_max_tokens_truncates(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i, "name": "x" * 2000} for i in range(50)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter, max_tokens=200)
            assert isinstance(result, list)
            assert len(result) < 50
        finally:
            await client.aclose()

    async def test_fetch_with_max_tokens_and_meta_sets_truncated(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i, "name": "x" * 2000} for i in range(50)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter, max_tokens=200, include_meta=True)
            assert isinstance(result, dict)
            assert result["_meta"]["truncated"] is True
            assert result["_meta"]["truncated_at"] is not None
            assert len(result["data"]) < 50
        finally:
            await client.aclose()

    async def test_fetch_within_budget_not_truncated(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i, "name": "short"} for i in range(3)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter, max_tokens=10_000, include_meta=True)
            assert result["_meta"]["truncated"] is False
            assert result["_meta"]["truncated_at"] is None
            assert len(result["data"]) == 3
        finally:
            await client.aclose()

    async def test_fetch_max_tokens_without_meta_still_truncates(self) -> None:
        adapter = _make_adapter()
        records = [{"id": i, "name": "x" * 2000} for i in range(50)]
        liquid, client = await _make_liquid(records)
        try:
            result = await liquid.fetch(adapter, max_tokens=200)
            # No meta requested, but truncation still applied.
            assert isinstance(result, list)
            assert len(result) < 50
        finally:
            await client.aclose()
